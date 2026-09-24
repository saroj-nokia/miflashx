"""
command_runner.py — replaces the old core.py._execute_command().

Fixes vs. the old implementation:
  * No more reading stdout-to-completion before touching stderr. That pattern
    deadlocks the moment a process (fastboot in particular) writes enough to
    stderr while nobody's draining it. Streams are merged instead.
  * Every call has a timeout. A wedged `fastboot devices` can no longer hang
    the app forever.
  * Returns a plain dataclass instead of a (bool, list_or_None) tuple, so
    callers can't misread which branch they're in.
  * Safe to call from any thread — it does no direct GUI work. The GUI layer
    is responsible for running this inside a QThread/worker and connecting
    a signal to the result.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field


@dataclass
class CommandResult:
    ok: bool
    returncode: int | None
    output: str          # merged stdout+stderr, already stripped/split ready
    timed_out: bool = False
    error: str | None = None   # populated on FileNotFoundError / launch failure

    @property
    def lines(self) -> list[str]:
        return [l for l in self.output.splitlines() if l.strip()]


def run_command(
    command: list[str],
    cwd: str | None = None,
    timeout: float = 10.0,
    env: dict | None = None,
) -> CommandResult:
    """
    Run `command` and block until it finishes, times out, or fails to launch.
    Intended to be called from a worker thread, never the GUI thread.
    """
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # <-- the key fix: one stream, no deadlock
            text=True,
            env=full_env,
            timeout=timeout,
        )
        return CommandResult(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            output=proc.stdout or "",
        )

    except subprocess.TimeoutExpired as e:
        partial = e.output if isinstance(e.output, str) else (e.output or b"").decode("utf-8", "ignore")
        return CommandResult(
            ok=False,
            returncode=None,
            output=partial,
            timed_out=True,
            error=f"Command timed out after {timeout}s: {' '.join(command)}",
        )

    except FileNotFoundError:
        return CommandResult(
            ok=False,
            returncode=None,
            output="",
            error=f"Executable not found: {command[0]}",
        )

    except Exception as e:  # noqa: BLE001 — last-resort guard so a worker thread
        return CommandResult(  # never dies silently and takes detection down with it
            ok=False,
            returncode=None,
            output="",
            error=f"Unexpected error running {' '.join(command)}: {e}",
        )
