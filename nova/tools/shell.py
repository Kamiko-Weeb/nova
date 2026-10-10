"""Shell execution. On Windows this runs PowerShell; elsewhere, the system shell."""
from __future__ import annotations

import os
import subprocess
import sys

from ..config import Config
from .registry import Registry, format_process, schema, string

IS_WINDOWS = sys.platform == "win32"


def register(registry: Registry, cfg: Config) -> None:
    shell_name = "PowerShell" if IS_WINDOWS else "bash"

    @registry.define(
        "run_shell",
        description=(
            f"Run a {shell_name} command on the user's computer and return its combined "
            "stdout and stderr. Use this for launching applications, querying system "
            "state, managing processes, installing packages, and anything else a person "
            "would do from a terminal. Commands are non-interactive: they cannot prompt "
            "for input, so pass flags like -y or -Confirm:$false where needed. "
            + (
                "This is Windows PowerShell 5.1: '&&' and '||' are NOT valid, use ';' "
                "or 'if ($?) { ... }' instead."
                if IS_WINDOWS
                else ""
            )
        ),
        parameters=schema(
            {
                "command": string(f"The {shell_name} command to execute."),
                "working_dir": string(
                    "Optional directory to run the command in.", default=""
                ),
            },
            ["command"],
        ),
        dangerous=True,
        preview=lambda a: f"Run {shell_name}:\n{a.get('command', '')}",
    )
    def run_shell(command: str, working_dir: str = "") -> str:
        cwd = working_dir or str(cfg.workspace)
        if not os.path.isdir(cwd):
            cwd = str(cfg.workspace)

        if IS_WINDOWS:
            argv = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ]
        else:
            argv = ["/bin/bash", "-lc", command]

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=cfg.shell_timeout,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return (
                f"ERROR: command exceeded the {cfg.shell_timeout}s timeout and was "
                "killed. If it is a long-running or interactive program, start it "
                "detached instead (e.g. Start-Process)."
            )
        except FileNotFoundError as exc:
            return f"ERROR: could not start the shell: {exc}"

        return format_process(proc.returncode, proc.stdout, proc.stderr, "(no output)")
