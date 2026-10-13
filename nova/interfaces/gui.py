"""Desktop chat window, built on tkinter so it needs no extra packages.

The agent runs on a worker thread. Everything the worker wants to show is pushed
onto a queue and drained on the UI thread, which is the only one allowed to touch
widgets. Approval requests travel the same way, with the worker blocking on an
Event until the dialog comes back.
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import font as tkfont
from tkinter import scrolledtext
from typing import Any

from ..agent import Event
from ..app import build_session
from ..approval import CallbackApprover
from ..config import Config

BG = "#13151a"
PANEL = "#1b1e26"
FIELD = "#232734"
FG = "#e6e8ee"
MUTED = "#8b93a7"
ACCENT = "#76b3ff"
GREEN = "#7bd88f"
AMBER = "#ffcc66"
RED = "#ff7a7a"


def _font(size: int, *, bold: bool = False, mono: bool = False) -> tkfont.Font:
    """Segoe UI and Cascadia Mono only exist on Windows. Elsewhere Tk quietly
    substitutes the system font, which left bold text regular and code in a
    proportional face, so ask for the platform's own fonts instead."""
    if sys.platform == "win32":
        family = "Cascadia Mono" if mono else "Segoe UI Semibold" if bold else "Segoe UI"
        return tkfont.Font(family=family, size=size)
    family = "Menlo" if mono else tkfont.nametofont("TkDefaultFont").actual("family")
    return tkfont.Font(family=family, size=size, weight="bold" if bold else "normal")


@dataclass
class ApprovalRequest:
    tool_name: str
    description: str
    event: threading.Event
    answer: Any = False
    dialog: tk.Toplevel | None = field(default=None, repr=False)


class NovaWindow:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.ui_queue: queue.Queue[Any] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.busy = False
        self.streaming = False
        self.pending_approval: ApprovalRequest | None = None

        self.approver = CallbackApprover(self._request_approval)
        self.session = build_session(
            cfg, approver=self.approver, on_event=self._from_worker
        )
        self.llm = self.session.llm
        self.memory = self.session.memory
        self.registry = self.session.registry
        self.agent = self.session.agent

        self._build_window()
        self._greet()
        self.root.after(60, self._drain)

    # --------------------------------------------------------------- widgets
    def _build_window(self) -> None:
        self.root = tk.Tk()
        self.root.title("Nova")
        self.root.geometry("960x720")
        self.root.minsize(640, 440)
        self.root.configure(bg=BG)

        body = _font(10)
        bold = _font(10, bold=True)
        small = _font(9)
        mono = _font(9, mono=True)

        header = tk.Frame(self.root, bg=PANEL, height=44)
        header.pack(fill="x")
        tk.Label(
            header, text="Nova", bg=PANEL, fg=FG, font=_font(13, bold=True),
        ).pack(side="left", padx=14)
        tk.Label(
            header, text=self.session.subtitle("  ·  "), bg=PANEL, fg=MUTED, font=small
        ).pack(side="left")
        tk.Button(
            header, text="Clear", command=self._clear, bg=PANEL, fg=MUTED,
            activebackground=PANEL, activeforeground=FG, relief="flat",
            borderwidth=0, cursor="hand2", font=body,
        ).pack(side="right", padx=12)

        self.transcript = scrolledtext.ScrolledText(
            self.root, wrap="word", bg=BG, fg=FG, insertbackground=FG,
            font=body, relief="flat", borderwidth=0,
            padx=16, pady=12, state="disabled", spacing1=2, spacing3=6,
        )
        self.transcript.pack(fill="both", expand=True)

        self.transcript.tag_config("you", foreground=ACCENT, font=bold)
        self.transcript.tag_config("nova", foreground=GREEN, font=bold)
        self.transcript.tag_config("text", foreground=FG)
        self.transcript.tag_config("muted", foreground=MUTED, font=small)
        self.transcript.tag_config("tool", foreground=AMBER, font=mono,
                                   lmargin1=16, lmargin2=16)
        self.transcript.tag_config("error", foreground=RED)

        footer = tk.Frame(self.root, bg=PANEL)
        footer.pack(fill="x")

        self.entry = tk.Text(
            footer, height=3, wrap="word", bg=FIELD, fg=FG, insertbackground=FG,
            font=body, relief="flat", borderwidth=0, padx=10, pady=8,
        )
        self.entry.pack(side="left", fill="both", expand=True, padx=(12, 8), pady=10)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<Shift-Return>", lambda _e: None)
        self.entry.bind("<Escape>", lambda _e: self._stop())
        self.entry.focus_set()

        # Side by side, so the footer never grows taller than the input box and
        # clips a button off the bottom of the window.
        buttons = tk.Frame(footer, bg=PANEL)
        buttons.pack(side="right", padx=(0, 12), pady=10)
        self.stop_button = tk.Button(
            buttons, text="Stop", command=self._stop, bg="#3a2a2f", fg=RED,
            activebackground="#4a3238", relief="flat", borderwidth=0, width=7,
            cursor="hand2", font=bold, state="disabled",
        )
        self.stop_button.pack(side="left", padx=(0, 8), ipady=10)
        self.send_button = tk.Button(
            buttons, text="Send", command=self._submit, bg=ACCENT, fg="#0b1220",
            activebackground="#9ac7ff", relief="flat", borderwidth=0, width=9,
            cursor="hand2", font=bold,
        )
        self.send_button.pack(side="left", ipady=10)

        status = tk.Frame(self.root, bg=PANEL)
        status.pack(fill="x")
        self.status = tk.Label(
            status, text="Ready", bg=PANEL, fg=MUTED, anchor="w",
            font=small, padx=14,
        )
        self.status.pack(side="left")
        self.usage_label = tk.Label(
            status, text="", bg=PANEL, fg=MUTED, anchor="e", font=small, padx=14
        )
        self.usage_label.pack(side="right")

        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------- transcript
    def _write(self, text: str, tag: str = "text") -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text, tag)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _end_stream(self) -> None:
        if self.streaming:
            self._write("\n\n", "text")
            self.streaming = False

    def _greet(self) -> None:
        self._write("Nova\n", "nova")
        self._write(
            "Tell me what to do and I will do it. I can run shell commands, read and "
            "write files, search the web, read and drive application windows, look at "
            "your screen, and control the mouse and keyboard.\n\n",
            "text",
        )
        if self.cfg.confirm_dangerous:
            self._write(
                "Actions that change something will ask you first. "
                "Esc or the Stop button interrupts a run.\n\n",
                "muted",
            )
        else:
            self._write(
                "Confirmation is OFF - actions run without asking.\n\n", "error"
            )

    def _clear(self) -> None:
        if self.busy:
            return
        self.agent.reset()
        if isinstance(self.approver, CallbackApprover):
            self.approver.reset()
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self.streaming = False
        self._greet()

    # ------------------------------------------------------------------ input
    def _on_return(self, _event: tk.Event) -> str:
        self._submit()
        return "break"  # keep Text from inserting a newline

    def _submit(self) -> None:
        if self.busy:
            return
        instruction = self.entry.get("1.0", "end").strip()
        if not instruction:
            return
        self.entry.delete("1.0", "end")
        self._write("You\n", "you")
        self._write(instruction + "\n\n", "text")
        self._set_busy(True)

        self.worker = threading.Thread(
            target=self._work, args=(instruction,), daemon=True
        )
        self.worker.start()

    def _stop(self) -> None:
        if not self.busy:
            return
        self.status.configure(text="Stopping...")
        self.agent.stop()
        # If a confirmation dialog is open, the worker is blocked on it; answering
        # "no" lets it unwind and notice the cancellation.
        request = self.pending_approval
        if request is not None and request.dialog is not None:
            request.answer = False
            request.event.set()
            try:
                request.dialog.destroy()
            except tk.TclError:
                pass
            self.pending_approval = None

    def _work(self, instruction: str) -> None:
        try:
            self.agent.run(instruction)
        except Exception as exc:  # never let the worker die silently
            self._from_worker(Event("error", f"Unexpected failure: {exc}"))
        finally:
            self.ui_queue.put(("done", None))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_button.configure(
            state="disabled" if busy else "normal",
            text="Working" if busy else "Send",
        )
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.status.configure(text="Working..." if busy else "Ready")
        if not busy:
            self._refresh_usage()

    def _refresh_usage(self) -> None:
        session = self.llm.usage
        if session.calls:
            self.usage_label.configure(text=f"this session: {session.summary()}")

    # ------------------------------------------------- worker -> UI plumbing
    def _from_worker(self, event: Event) -> None:
        self.ui_queue.put(("event", event))

    def _request_approval(self, tool_name: str, description: str) -> bool | str:
        request = ApprovalRequest(tool_name, description, threading.Event())
        self.ui_queue.put(("approve", request))
        request.event.wait()
        return request.answer

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "event":
                    self._show_event(payload)
                elif kind == "approve":
                    self._show_approval(payload)
                elif kind == "done":
                    self._end_stream()
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(60, self._drain)

    def _show_event(self, event: Event) -> None:
        if event.kind == "text":
            if not self.streaming:
                self._write("Nova\n", "nova")
                self.streaming = True
            self._write(event.text, "text")
            return

        if event.kind in {"tool", "result", "denied", "error", "stopped"}:
            self._end_stream()

        if event.kind == "step":
            self.status.configure(text=event.text.splitlines()[0][:120])
            if not event.text.startswith("Thinking (step"):
                self._write(event.text + "\n", "muted")
        elif event.kind == "tool":
            self._write(f"> {event.detail}\n", "tool")
        elif event.kind == "result":
            lines = event.detail.strip().splitlines()
            preview = lines[0][:160] if lines else "(no output)"
            extra = f" (+{len(lines) - 1} more lines)" if len(lines) > 1 else ""
            self._write(f"  {preview}{extra}\n", "muted")
            self._refresh_usage()
        elif event.kind == "denied":
            self._write(f"  {event.text}\n", "error")
        elif event.kind == "stopped":
            self._write("Stopped.\n\n", "error")
        elif event.kind == "answer":
            if self.streaming:
                self._end_stream()
            else:
                self._write("\nNova\n", "nova")
                self._write(event.text + "\n\n", "text")
        elif event.kind == "error":
            self._write("\n" + event.text + "\n\n", "error")

    def _show_approval(self, request: ApprovalRequest) -> None:
        # A stop can land between the request being queued and this running.
        if self.agent.cancel.is_set():
            request.answer = False
            request.event.set()
            return

        self._end_stream()
        dialog = tk.Toplevel(self.root)
        request.dialog = dialog
        self.pending_approval = request
        dialog.title("Nova wants to do something")
        dialog.configure(bg=PANEL)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()

        tk.Label(
            dialog, text=f"Allow {request.tool_name}?", bg=PANEL, fg=FG,
            font=_font(11, bold=True),
        ).pack(anchor="w", padx=18, pady=(16, 6))

        detail = tk.Text(
            dialog, height=min(12, max(3, request.description.count("\n") + 2)),
            width=74, wrap="word", bg=FIELD, fg=FG, relief="flat",
            font=_font(9, mono=True), padx=10, pady=8,
        )
        detail.insert("1.0", request.description)
        detail.configure(state="disabled")
        detail.pack(padx=18)

        row = tk.Frame(dialog, bg=PANEL)
        row.pack(fill="x", padx=18, pady=16)

        def answer(value: bool | str) -> None:
            request.answer = value
            request.event.set()
            self.pending_approval = None
            try:
                dialog.destroy()
            except tk.TclError:
                pass

        def button(parent: tk.Misc, text: str, value: bool | str, bg: str, fg: str):
            return tk.Button(
                parent, text=text, command=lambda: answer(value), bg=bg, fg=fg,
                relief="flat", borderwidth=0, cursor="hand2", padx=14, pady=6,
                font=_font(10, bold=True),
            )

        button(row, "Deny", False, "#3a2a2f", RED).pack(side="right", padx=(8, 0))
        button(row, "Allow", True, ACCENT, "#0b1220").pack(side="right", padx=(8, 0))
        button(row, "Allow all like this", "always", "#2a3340", MUTED).pack(side="left")

        dialog.bind("<Escape>", lambda _e: answer(False))
        dialog.protocol("WM_DELETE_WINDOW", lambda: answer(False))
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + 120
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ------------------------------------------------------------------ life
    def _close(self) -> None:
        self.agent.stop()
        self.session.finish()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def launch(cfg: Config) -> None:
    # An installed app has no terminal to print setup instructions to, so ask
    # for the key in a window instead of exiting.
    from .first_run import ensure_key

    ready = ensure_key(cfg)
    if ready is None:
        return
    NovaWindow(ready).run()
