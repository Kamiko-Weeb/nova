"""First-run setup window.

A checkout can be told to edit .env by hand. An installed app cannot - its
bundle is read-only and the user never sees inside it - so when there is no
usable key we ask for one and write it to the writable data directory.
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import font as tkfont

from ..config import DATA_ROOT, Config

BG = "#13151a"
PANEL = "#1b1e26"
FIELD = "#232734"
FG = "#e6e8ee"
MUTED = "#8b93a7"
ACCENT = "#76b3ff"
RED = "#ff7a7a"

SIGNUP_URL = "https://build.nvidia.com"


def looks_like_key(value: str) -> bool:
    value = value.strip()
    if not value.startswith("nvapi-") or len(value) < 26:
        return False
    body = value[len("nvapi-"):].lower()
    return not any(w in body for w in ("replace", "your", "xxx", "here", "paste"))


def write_key(key: str) -> "object":
    """Save the key to the data directory's .env, preserving other settings."""
    path = DATA_ROOT / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if path.exists():
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("NVIDIA_API_KEY=")
        ]
    lines.insert(0, f"NVIDIA_API_KEY={key.strip()}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ask_for_key() -> bool:
    """Show the setup window. Returns True if a key was saved."""
    root = tk.Tk()
    root.title("Set up Nova")
    root.configure(bg=BG)
    root.resizable(False, False)

    body = tkfont.Font(family="Helvetica", size=11)
    bold = tkfont.Font(family="Helvetica", size=15, weight="bold")
    small = tkfont.Font(family="Helvetica", size=10)
    mono = tkfont.Font(family="Menlo", size=11)

    saved = {"ok": False}

    tk.Label(root, text="Nova needs an NVIDIA API key", bg=BG, fg=FG, font=bold).pack(
        anchor="w", padx=24, pady=(22, 6)
    )
    tk.Label(
        root,
        text=(
            "It is free. Sign up at build.nvidia.com, open your profile menu,\n"
            "choose API Keys, and generate one. It starts with ‘nvapi-’\n"
            "and is only shown once, so copy it straight away."
        ),
        bg=BG, fg=MUTED, font=small, justify="left",
    ).pack(anchor="w", padx=24)

    tk.Button(
        root, text="Open build.nvidia.com", bg=PANEL, fg=ACCENT, font=small,
        relief="flat", borderwidth=0, cursor="hand2", padx=12, pady=5,
        activebackground=PANEL, activeforeground=FG,
        command=lambda: webbrowser.open(SIGNUP_URL),
    ).pack(anchor="w", padx=24, pady=12)

    entry = tk.Entry(
        root, width=52, bg=FIELD, fg=FG, insertbackground=FG, font=mono,
        relief="flat", borderwidth=0,
    )
    entry.pack(anchor="w", padx=24, ipady=8, ipadx=8)
    entry.focus_set()

    status = tk.Label(root, text="", bg=BG, fg=RED, font=small)
    status.pack(anchor="w", padx=24, pady=(6, 0))

    def save() -> None:
        value = entry.get().strip()
        if not looks_like_key(value):
            status.configure(
                text="That does not look like a key. It should start with 'nvapi-'."
            )
            return
        path = write_key(value)
        saved["ok"] = True
        status.configure(fg=MUTED, text=f"Saved to {path}")
        root.after(400, root.destroy)

    row = tk.Frame(root, bg=BG)
    row.pack(fill="x", padx=24, pady=18)
    tk.Button(
        row, text="Save and start", command=save, bg=ACCENT, fg="#0b1220",
        font=body, relief="flat", borderwidth=0, cursor="hand2", padx=18, pady=7,
        activebackground="#9ac7ff",
    ).pack(side="left")
    tk.Button(
        row, text="Quit", command=root.destroy, bg=PANEL, fg=MUTED, font=body,
        relief="flat", borderwidth=0, cursor="hand2", padx=18, pady=7,
        activebackground=PANEL, activeforeground=FG,
    ).pack(side="left", padx=(10, 0))

    entry.bind("<Return>", lambda _e: save())
    root.bind("<Escape>", lambda _e: root.destroy())

    root.update_idletasks()
    width, height = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 3
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return saved["ok"]


def ensure_key(cfg: Config) -> Config | None:
    """Return a usable config, asking for a key if there is not one yet."""
    if cfg.has_api_key:
        return cfg
    if not ask_for_key():
        return None
    # Re-read: load_dotenv only fills values that are not already set, so clear
    # the stale blank first.
    import os

    os.environ.pop("NVIDIA_API_KEY", None)
    refreshed = Config.from_env()
    return refreshed if refreshed.has_api_key else None
