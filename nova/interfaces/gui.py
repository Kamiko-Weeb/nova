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
from pathlib import Path
from tkinter import font as tkfont
from tkinter import scrolledtext
from typing import Any, Iterable

from .. import permissions
from ..agent import Event
from ..app import build_session
from ..approval import CallbackApprover
from ..attachments import (
    DEFAULT_PROMPT,
    MAX_ATTACHMENTS,
    add_paths,
    badge,
    compose_message,
    describe,
    save_pasted_image,
    summary_line,
)
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

PERMISSION_CHECK_MS = 2000
# macOS draws a tk.Button with its own light face whatever bg says, so button
# text has to be dark there; Windows honours the dark panel colour.
BUTTON_TEXT = "#0b1220" if sys.platform == "darwin" else FG

# Attachment chips. The border gives each chip a visible edge: the fill alone
# is 1.12:1 against the panel, this border 3.2:1.
CHIP_BORDER = "#646d86"
CHIP_HOVER = "#2c3242"
MAX_CHIPS = 6  # chips shown before "+N more"
CHIP_NAME_CHARS = 28
MOD = "Command" if sys.platform == "darwin" else "Control"  # for key bindings
KEYS = "⌘" if sys.platform == "darwin" else "Ctrl+"  # for people
ATTACH_HINT = (
    f"Attach files ({KEYS}O). You can also drag them onto the window, "
    f"or paste a screenshot with {KEYS}V."
)
# A drag passing from one part of the window to another reports leaving the
# first just before entering the second; waiting this long avoids a flicker.
DRAG_LEAVE_MS = 80
# A key release followed this quickly by another press is key repeat (the
# X11 way of reporting a held key), not the key being let go.
BACKSPACE_RELEASE_MS = 50


def _make_root() -> tuple[tk.Tk, bool]:
    """The window's root, able to take dropped files when tkinterdnd2 loads."""
    try:
        from tkinterdnd2 import TkinterDnD

        return TkinterDnD.Tk(), True
    except Exception:  # not installed, or its Tk extension would not load
        return tk.Tk(), False


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
        self._drag_leave_job: str | None = None
        self._backspace_held = False
        self._backspace_release_job: str | None = None

        self.attachments: list[Path] = []

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
        if self.permissions_button is not None:
            self._check_permissions()

    # --------------------------------------------------------------- widgets
    def _build_window(self) -> None:
        self.root, self.can_drop = _make_root()
        self.root.title("Nova")
        self.root.geometry("960x720")
        self.root.minsize(640, 440)
        self.root.configure(bg=BG)

        body = _font(10)
        bold = _font(10, bold=True)
        small = self.small_font = _font(9)
        self.badge_font = _font(9, bold=True)
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
        # macOS only: green when Nova has every permission it needs, red - and a
        # click away from the right page in System Settings - when it does not.
        # A Label, because macOS ignores the colours of a tk.Button.
        self.permissions_button: tk.Label | None = None
        if permissions.PERMISSIONS:
            self.permissions_button = tk.Label(
                header, fg="#0b1220", font=_font(9, bold=True), padx=10, pady=3,
                cursor="hand2",
            )
            self.permissions_button.pack(side="right")
            self.permissions_button.bind("<Button-1>", self._fix_permissions)
            self.permissions_button.bind("<Enter>", self._describe_permissions)
            self.permissions_button.bind("<Leave>", lambda _e: self._restore_status())

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

        # Attachment chips sit above the message box, packed only while there
        # are any (see _render_chips).
        self.chip_row = tk.Frame(self.root, bg=PANEL)
        footer = self.footer = tk.Frame(self.root, bg=PANEL)
        footer.pack(fill="x")

        self.entry = tk.Text(
            footer, height=3, wrap="word", bg=FIELD, fg=FG, insertbackground=FG,
            font=body, relief="flat", borderwidth=0, padx=10, pady=8,
        )
        self.entry.pack(side="left", fill="both", expand=True, padx=(12, 8), pady=10)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<Shift-Return>", lambda _e: None)
        self.entry.bind("<Escape>", lambda _e: self._stop())
        self.entry.bind("<BackSpace>", self._on_backspace)
        self.entry.bind("<KeyRelease-BackSpace>", self._on_backspace_release)
        self.entry.bind(f"<{MOD}-v>", self._paste)
        # The keyboard way to attach. Bound on the message box too, because its
        # own Ctrl+O binding (open a new line) would otherwise run first.
        for widget in (self.entry, self.root):
            widget.bind(f"<{MOD}-o>", self._choose_files)
        self.entry.focus_set()

        # Side by side, so the footer never grows taller than the input box and
        # clips a button off the bottom of the window.
        buttons = tk.Frame(footer, bg=PANEL)
        buttons.pack(side="right", padx=(0, 12), pady=10)
        self.attach_button = tk.Button(
            buttons, text="Attach", command=self._choose_files, bg=PANEL, fg=BUTTON_TEXT,
            activebackground=PANEL, relief="flat", borderwidth=0, width=7,
            cursor="hand2", font=bold,
        )
        self.attach_button.pack(side="left", padx=(0, 8), ipady=10)
        # Hovering explains the ways to attach that have no button of their own.
        self.attach_button.bind("<Enter>", lambda _e: self.status.configure(text=ATTACH_HINT))
        self.attach_button.bind("<Leave>", lambda _e: self._restore_status())
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

        # The message box's own focus ring, restored after a drag recolours it.
        self._entry_ring = (
            self.entry.cget("highlightbackground"), self.entry.cget("highlightcolor")
        )
        if self.can_drop:
            from tkinterdnd2 import DND_FILES

            for widget in (self.root, self.transcript, self.entry):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<DropEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DropLeave>>", self._on_drag_leave)
                widget.dnd_bind("<<Drop>>", self._on_drop)

        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------ permissions
    def _check_permissions(self) -> None:
        """Recolour the permissions button, then check again shortly, so it
        turns green by itself once the switch is flipped in System Settings."""
        missing = permissions.missing()
        self.permissions_button.configure(
            text="✕ Permissions" if missing else "✓ Permissions",
            bg=RED if missing else GREEN,
        )
        self.root.after(PERMISSION_CHECK_MS, self._check_permissions)

    def _describe_permissions(self, _event: tk.Event | None = None) -> None:
        missing = permissions.missing()
        states = "  ·  ".join(
            f"{p.name}: {'off' if p in missing else 'on'}" for p in permissions.PERMISSIONS
        )
        self.status.configure(text=states + ("  -  click to fix" if missing else ""))

    def _fix_permissions(self, _event: tk.Event | None = None) -> None:
        missing = permissions.missing()
        if not missing:
            self.status.configure(text="Every permission Nova needs is on.")
            return
        first = missing[0]
        first.open_settings()
        restart = " Then quit and reopen Nova." if first.needs_restart else ""
        self.status.configure(
            text=f"Turn on Nova (or your terminal) in the System Settings window that "
            f"opened, so it can {first.purpose}.{restart}"
        )

    # ------------------------------------------------------------ attachments
    def _choose_files(self, _event: tk.Event | None = None) -> str:
        from tkinter import filedialog

        self._add_attachments(
            filedialog.askopenfilenames(parent=self.root, title="Attach files")
        )
        return "break"  # as a shortcut, it must not also reach the message box

    def _add_attachments(self, candidates: Iterable[str | Path]) -> None:
        before = len(self.attachments)
        self.attachments, left_out = add_paths(self.attachments, candidates)
        added = self.attachments[before:]
        self._render_chips()
        if left_out:
            plural = "s" if left_out != 1 else ""
            self.status.configure(
                text=f"Left out {left_out} file{plural}: at most {MAX_ATTACHMENTS} per message."
            )
        elif len(added) == 1:
            self.status.configure(text=f"Attached {added[0].name}")
        elif added:
            self.status.configure(text=f"Attached {len(added)} files")
        self.entry.focus_set()

    def _remove_attachment(self, path: Path) -> None:
        self.attachments = [p for p in self.attachments if p != path]
        self._render_chips()
        self._restore_status()

    def _on_backspace(self, _event: tk.Event | None = None) -> str | None:
        """Backspace in an empty message box takes off the last attachment - but
        only on a fresh press. A key held down to clear the text keeps repeating
        once the text is gone, and must not strip the attachments as well."""
        if self._backspace_release_job is not None:  # released and pressed at once
            self.root.after_cancel(self._backspace_release_job)
            self._backspace_release_job = None
        repeat = self._backspace_held
        self._backspace_held = True
        if repeat or not self.attachments or self.entry.get("1.0", "end-1c"):
            return None
        removed = self.attachments[-1]
        self._remove_attachment(removed)
        self.status.configure(text=f"Removed {removed.name}")
        return "break"

    def _on_backspace_release(self, _event: tk.Event | None = None) -> None:
        self._backspace_release_job = self.root.after(
            BACKSPACE_RELEASE_MS, self._backspace_up
        )

    def _backspace_up(self) -> None:
        self._backspace_held = False
        self._backspace_release_job = None

    def _render_chips(self) -> None:
        for child in self.chip_row.winfo_children():
            child.destroy()
        if not self.attachments:
            self.chip_row.pack_forget()
            return
        for path in self.attachments[:MAX_CHIPS]:
            self._make_chip(path)
        hidden = len(self.attachments) - MAX_CHIPS
        if hidden > 0:
            tk.Label(
                self.chip_row, text=f"+{hidden} more", bg=PANEL, fg=MUTED,
                font=self.small_font,
            ).pack(side="left", pady=(8, 0))
        self.chip_row.pack(fill="x", padx=12, before=self.footer)

    def _make_chip(self, path: Path) -> None:
        """One attachment: a type tag, its name, and a ✕ that removes it.
        Hovering anywhere on it shows the full path and size."""
        name = path.name + ("/" if path.is_dir() else "")
        if len(name) > CHIP_NAME_CHARS:
            name = name[: CHIP_NAME_CHARS - 1] + "…"
        chip = tk.Frame(
            self.chip_row, bg=FIELD, highlightthickness=1,
            highlightbackground=CHIP_BORDER, highlightcolor=CHIP_BORDER,
        )
        chip.pack(side="left", padx=(0, 8), pady=(8, 0))
        tk.Label(chip, text=badge(path), bg=FIELD, fg=ACCENT, font=self.badge_font).pack(
            side="left", padx=(8, 4), pady=4
        )
        tk.Label(chip, text=name, bg=FIELD, fg=FG, font=self.small_font).pack(
            side="left", pady=4
        )
        remove = tk.Label(
            chip, text="✕", bg=FIELD, fg=MUTED, font=self.small_font, cursor="hand2",
            padx=8, pady=4,
        )
        remove.pack(side="left")
        remove.bind("<Button-1>", lambda _e: self._remove_attachment(path))
        remove.bind("<Enter>", lambda _e: remove.configure(fg=RED))
        remove.bind("<Leave>", lambda _e: remove.configure(fg=MUTED))

        parts = [chip, *chip.winfo_children()]

        def paint(fill: str, edge: str) -> None:
            for widget in parts:
                widget.configure(bg=fill)
            chip.configure(highlightbackground=edge, highlightcolor=edge)

        def enter(_event: tk.Event) -> None:
            # The fill change alone is too faint to notice; the edge makes it clear.
            paint(CHIP_HOVER, MUTED)
            self.status.configure(text=describe(path))

        def leave(_event: tk.Event) -> None:
            # Moving between the chip's own parts is not leaving the chip.
            if self.root.winfo_containing(*self.root.winfo_pointerxy()) not in parts:
                paint(FIELD, CHIP_BORDER)
                self._restore_status()

        for widget in parts:
            widget.bind("<Enter>", enter, add="+")
            widget.bind("<Leave>", leave, add="+")

    def _restore_status(self) -> None:
        self.status.configure(text="Working..." if self.busy else "Ready")

    def _paste(self, _event: tk.Event | None = None) -> str | None:
        """Attach a pasted image (or, on Windows, copied files). Anything else
        falls through to the normal text paste."""
        try:
            from PIL import ImageGrab

            grabbed = ImageGrab.grabclipboard()
        except Exception:
            return None
        if grabbed is None:
            return None
        if isinstance(grabbed, list):  # file paths, which Windows provides
            self._add_attachments(grabbed)
        else:
            pasted = save_pasted_image(grabbed)
            self._add_attachments([pasted])
            if pasted.resolve() in self.attachments:  # not left out over the limit
                self.status.configure(text="Attached the pasted screenshot")
        return "break"

    def _on_drag_enter(self, event: Any) -> str:
        if self._drag_leave_job is not None:  # still over the window
            self.root.after_cancel(self._drag_leave_job)
            self._drag_leave_job = None
        self._show_drop_target(True)
        return event.action

    def _on_drag_leave(self, event: Any) -> str:
        if self._drag_leave_job is not None:
            self.root.after_cancel(self._drag_leave_job)
        self._drag_leave_job = self.root.after(
            DRAG_LEAVE_MS, lambda: self._show_drop_target(False)
        )
        return event.action

    def _on_drop(self, event: Any) -> str:
        if self._drag_leave_job is not None:
            self.root.after_cancel(self._drag_leave_job)
        self._show_drop_target(False)
        # Tk hands over one string; paths with spaces come wrapped in braces.
        self._add_attachments(self.root.tk.splitlist(event.data))
        return event.action

    def _show_drop_target(self, active: bool) -> None:
        """While files are dragged over the window, tint the conversation and
        outline the message box, so it is obvious they can be dropped."""
        self._drag_leave_job = None
        self.transcript.configure(bg=FIELD if active else BG)
        ring = (ACCENT, ACCENT) if active else self._entry_ring
        self.entry.configure(highlightbackground=ring[0], highlightcolor=ring[1])
        if active:
            self.status.configure(text="Drop to attach")
        else:
            self._restore_status()

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
        typed = self.entry.get("1.0", "end").strip()
        if not typed and not self.attachments:
            return
        instruction = compose_message(typed, self.attachments)
        self.entry.delete("1.0", "end")
        self._write("You\n", "you")
        self._write((typed or DEFAULT_PROMPT) + "\n", "text")
        if self.attachments:
            self._write(summary_line(self.attachments) + "\n", "muted")
        self._write("\n", "text")
        self.attachments = []
        self._render_chips()
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
