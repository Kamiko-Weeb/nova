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
