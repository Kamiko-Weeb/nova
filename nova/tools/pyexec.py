"""A Python escape hatch.

This is what lets the agent handle tasks nobody wrote a tool for: parsing a
spreadsheet, doing arithmetic properly, calling a library, reshaping data. The
model writes a short script; whatever it prints comes back.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from ..config import Config
from .registry import Registry, format_process, integer, schema, string


def register(registry: Registry, cfg: Config) -> None:
    @registry.define(
        "run_python",
        description=(
            "Execute a short Python 3 script in a separate process and return whatever "
            "it prints. Use this for calculations, data processing, parsing files, or "
            "anything where a few lines of Python beats a shell command. You must "
            "print() the results you want to see. The standard library is available, "
            "plus httpx, PIL, bs4 and pyautogui."
        ),
        parameters=schema(
            {
                "code": string("The Python source to run."),
                "timeout": integer("Seconds to allow before killing it.", default=60),
            },
            ["code"],
        ),
        dangerous=True,
        preview=lambda a: f"Run Python:\n{a.get('code', '')}",
    )
    def run_python(code: str, timeout: int = 60) -> str:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(code)
            script = Path(handle.name)
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, min(600, timeout)),
                cwd=str(cfg.workspace),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: script exceeded {timeout}s and was killed."
        finally:
            script.unlink(missing_ok=True)

        return format_process(
            proc.returncode,
            proc.stdout,
            proc.stderr,
            "(script printed nothing - remember to print() your results)",
        )
