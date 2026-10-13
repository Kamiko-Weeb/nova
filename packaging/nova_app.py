"""Entry point for the packaged application.

PyInstaller needs a single script with no command-line arguments, so this just
opens the GUI. Everything else is still reachable from a checkout via main.py.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

# When frozen the package sits beside this file inside the bundle; in a checkout
# it is one directory up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    # In a packaged app sys.executable is Nova itself, not a Python interpreter,
    # so the run_python tool starts it again with this flag to run its script.
    if len(sys.argv) == 3 and sys.argv[1] == "--run-python":
        return _run_script(sys.argv[2])
    try:
        from nova.config import Config
        from nova.interfaces import gui

        gui.launch(Config.from_env())
        return 0
    except SystemExit as exc:
        _report("Nova could not start", str(exc))
        return 1
    except Exception:
        _report("Nova hit an unexpected error", traceback.format_exc())
        return 1


def _run_script(path: str) -> int:
    """Run a script with the bundled interpreter, as `python script.py` would."""
    import runpy

    # A windowed Windows build may start without stdout/stderr, even when its
    # parent is capturing them; reopen the inherited handles so print() works.
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(fd, "w", encoding="utf-8", closefd=False))
            except OSError:
                pass
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


def _report(title: str, detail: str) -> None:
    """Show a failure in a window, since a packaged app has no console."""
    print(f"{title}\n{detail}", file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import scrolledtext

        root = tk.Tk()
        root.title(title)
        root.configure(bg="#13151a")
        tk.Label(
            root, text=title, bg="#13151a", fg="#ff7a7a",
            font=("Helvetica", 14, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 8))
        box = scrolledtext.ScrolledText(
            root, width=88, height=18, wrap="word", bg="#232734", fg="#e6e8ee",
            relief="flat", font=("Menlo", 10), padx=10, pady=8,
        )
        box.insert("1.0", detail)
        box.configure(state="disabled")
        box.pack(padx=20, pady=(0, 16))
        tk.Button(
            root, text="Close", command=root.destroy, bg="#2a3340", fg="#e6e8ee",
            relief="flat", borderwidth=0, padx=18, pady=6, cursor="hand2",
        ).pack(pady=(0, 18))
        root.mainloop()
    except Exception:
        pass  # no display available; the stderr print above is all we can do


if __name__ == "__main__":
    raise SystemExit(main())
