"""
device_monitor.py — replaces gui.py's QTimer(2000ms) + inline
FlashingCore.detect_device() call on the GUI thread.

Old approach: poll `fastboot devices` every 2s, on the GUI thread, with a
subprocess call that can deadlock (see command_runner.py's docstring). Feels
laggy even when it isn't stuck, and freezes the whole app when it is.

New approach:
  * pyudev watches the kernel's USB subsystem directly (netlink), so we react
    to real plug/unplug events instantly instead of polling blind.
  * pyudev's MonitorObserver already runs its own background thread — we
    never touch the GUI thread here.
  * A plug/unplug event only tells us *something* changed on the USB bus.
    We still confirm fastboot state by shelling out to `fastboot devices`,
    but only on that trigger (plus a slow 5s safety-net poll in case udev
    misses an event on Wayland+bad-driver combos) — not every 2s regardless.
  * All confirmation runs through command_runner.run_command with a timeout,
    so a wedged fastboot binary can't hang device detection anymore.

Known vendor IDs watched: Google (18d1), Xiaomi (2717). Extend as needed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from command_runner import run_command

try:
    import pyudev
except ImportError:  # pragma: no cover - surfaced clearly at startup instead of a bare crash
    pyudev = None

ANDROID_VENDOR_IDS = {"18d1", "2717"}
SAFETY_NET_POLL_MS = 5000  # udev should catch everything; this just covers edge cases


@dataclass
class DeviceState:
    connected: bool
    serial: str | None = None
    mode: str | None = None  # "fastboot", "fastbootd", or None


class _FastbootCheckWorker(QThread):
    """Runs `fastboot devices` off the GUI thread and reports the result."""
    result_ready = pyqtSignal(object)  # DeviceState

    def __init__(self, fastboot_path: str, parent=None):
        super().__init__(parent)
        self.fastboot_path = fastboot_path

    def run(self):
        if not self.fastboot_path:
            self.result_ready.emit(DeviceState(connected=False))
            return

        res = run_command([self.fastboot_path, "devices"], timeout=5.0)

        if res.timed_out:
            # A wedged fastboot process is itself useful information — surface
            # it distinctly from "no device" instead of silently reporting nothing.
            self.result_ready.emit(DeviceState(connected=False, mode="timeout"))
            return

        if res.ok:
            for line in res.lines:
                parts = line.split()
                if len(parts) >= 2 and "fastboot" in parts[1]:
                    self.result_ready.emit(
                        DeviceState(connected=True, serial=parts[0], mode=parts[1])
                    )
                    return

        self.result_ready.emit(DeviceState(connected=False))


class DeviceMonitor(QObject):
    """
    Emits device_changed(DeviceState) whenever fastboot device presence
    actually changes. Owns its pyudev observer thread and its fastboot-check
    worker thread; the GUI only ever receives signals.
    """
    device_changed = pyqtSignal(object)  # DeviceState

    def __init__(self, fastboot_path: str, parent=None):
        super().__init__(parent)
        self.fastboot_path = fastboot_path
        self._last_state = DeviceState(connected=False)
        self._check_worker: _FastbootCheckWorker | None = None
        self._udev_observer = None

        if pyudev is None:
            # No pyudev available (missing dependency) — fall back to the
            # safety-net poll alone rather than refusing to detect anything.
            pass
        else:
            self._start_udev_watch()

        # Safety net: catches the rare case udev doesn't fire (bad driver,
        # some Wayland+containerized setups). Cheap because it only runs the
        # actual check on a background thread, never on the GUI thread.
        self._safety_timer = QTimer(self)
        self._safety_timer.setInterval(SAFETY_NET_POLL_MS)
        self._safety_timer.timeout.connect(self._trigger_check)
        self._safety_timer.start()

        # Check once immediately on startup so we don't wait for the first event.
        self._trigger_check()

    def _start_udev_watch(self):
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem="usb")

        def _on_event(action, device):
            vendor_id = device.get("ID_VENDOR_ID", "")
            if vendor_id in ANDROID_VENDOR_IDS or action in ("add", "remove"):
                # Debounce: USB add/remove fires multiple sub-events per plug.
                # A tiny delay lets the device settle before we query fastboot.
                threading.Timer(0.3, self._trigger_check).start()

        self._udev_observer = pyudev.MonitorObserver(monitor, _on_event)
        self._udev_observer.start()

    def _trigger_check(self):
        if self._check_worker is not None and self._check_worker.isRunning():
            return  # a check is already in flight; don't pile them up
        self._check_worker = _FastbootCheckWorker(self.fastboot_path)
        self._check_worker.result_ready.connect(self._on_check_result)
        self._check_worker.start()

    def _on_check_result(self, state: DeviceState):
        if state != self._last_state:
            self._last_state = state
            self.device_changed.emit(state)

    def stop(self):
        self._safety_timer.stop()
        if self._udev_observer is not None:
            self._udev_observer.stop()
        if self._check_worker is not None:
            self._check_worker.wait(1000)
