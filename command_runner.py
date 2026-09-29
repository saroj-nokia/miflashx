"""
command_runner.py — replaces the old core.py._execute_command().

Fixes vs. the old implementation:
  * No more reading stdout-to-completion before touching stderr. That pattern
    deadlocks the moment a process (fastboot in particular) writes enough to
    stderr while nobody's draining it. Streams are merged instead.
  * Every call has a timeout. A wedged `fastboot devices` can no longer hang
    the app forever.
  * Output can be streamed line by line (`on_line`) while the command runs, so
    a multi-minute flash shows progress as it happens instead of dumping
    everything at the end.
  * On timeout the whole process group is terminated, not just the direct
    child. Flash scripts are shell scripts that spawn fastboot; killing only
    the shell would leave fastboot running against the device.
  * Returns a plain dataclass instead of a (bool, list_or_None) tuple, so
    callers can't misread which branch they're in.
  * Safe to call from any thread — it does no direct GUI work. The GUI layer
    is responsible for running this inside a QThread/worker and connecting
    a signal to the result.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable


@dataclass
class CommandResult:
    ok: bool
    returncode: int | None
    output: str          # merged stdout+stderr
    timed_out: bool = False
    error: str | None = None   # populated on FileNotFoundError / launch failure

    @property
    def lines(self) -> list[str]:
        return [l for l in self.output.splitlines() if l.strip()]


def _terminate_process_group(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """SIGTERM the process group, then SIGKILL whatever is still alive."""
    def _signal(sig):
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), sig)
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    _signal(signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _signal(signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass


def run_command(
    command: list[str],
    cwd: str | None = None,
    timeout: float = 10.0,
    env: dict | None = None,
    on_line: Callable[[str], None] | None = None,
) -> CommandResult:
    """
    Run `command` and block until it finishes, times out, or fails to launch.
    Intended to be called from a worker thread, never the GUI thread.

    If `on_line` is given it is called with each non-empty output line as soon
    as the process writes it (from a helper thread — keep it thread-safe, e.g.
    emit a Qt signal). An exception raised by the callback is swallowed: if the
    reader stopped draining the pipe, the child would block on a full pipe.
    """
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,      # one stream: nothing to deadlock on
            encoding="utf-8",
            errors="replace",              # odd bytes must not kill the reader
            bufsize=1,
            env=full_env,
            start_new_session=(os.name == "posix"),   # own process group
        )
    except FileNotFoundError:
        return CommandResult(ok=False, returncode=None, output="",
                             error=f"Executable not found: {command[0]}")
    except Exception as e:  # noqa: BLE001 — last-resort guard so a worker
        return CommandResult(ok=False, returncode=None, output="",  # thread never dies silently
                             error=f"Unexpected error running {' '.join(command)}: {e}")

    collected: list[str] = []

    def _reader() -> None:
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                collected.append(line)
                if on_line is not None and line.strip():
                    try:
                        on_line(line)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(proc)

    # Normally the pipe closes the moment the process exits. If a grandchild
    # inherited it and is still running, don't wait for it forever.
    reader.join(timeout=2.0)
    output = "\n".join(collected)

    if timed_out:
        return CommandResult(
            ok=False, returncode=None, output=output, timed_out=True,
            error=f"Command timed out after {timeout}s: {' '.join(command)}",
        )

    return CommandResult(ok=proc.returncode == 0, returncode=proc.returncode, output=output)