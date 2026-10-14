"""GUI checks for attachments.

They open a real Nova window and, for pasting, use the real clipboard - which
they overwrite - so they only run when asked:

    NOVA_GUI_TESTS=1 .venv/bin/python -m pytest tests/test_gui_attachments.py -q
"""
import os
import subprocess
import sys
import time
import types

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_GUI_TESTS") != "1" or sys.platform != "darwin",
    reason="opens a window and uses the clipboard; set NOVA_GUI_TESTS=1 on a Mac",
)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-" + "A1b2C3d4" * 6)  # dummy; nothing is sent
    monkeypatch.setenv("NOVA_LOG_DIR", str(tmp_path / "logs"))
    from nova import attachments

    monkeypatch.setattr(attachments, "ATTACHMENTS_DIR", tmp_path / "attachments")
    from nova.config import Config
    from nova.interfaces.gui import NovaWindow

    win = NovaWindow(Config.from_env())
    win.sent = []
    win.agent.run = lambda instruction: win.sent.append(instruction) or "done"
    win.root.update()
    yield win
    win.root.destroy()


def make_files(tmp_path, *names):
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(path.resolve())
    return paths


def chip_labels(win):
    labels = []
    for child in win.chip_row.winfo_children():
        parts = child.winfo_children()
        labels.append(" ".join(w.cget("text") for w in parts) if parts else child.cget("text"))
    return labels


def test_chips_appear_and_collapse(window, tmp_path):
    window._add_attachments(make_files(tmp_path, *[f"f{i}.txt" for i in range(8)]))
    window.root.update()
    labels = chip_labels(window)
    assert labels[:2] == ["TXT f0.txt ✕", "TXT f1.txt ✕"]
    assert labels[-1] == "+2 more" and len(labels) == 7
    assert window.chip_row.winfo_ismapped()


def test_long_names_are_shortened(window, tmp_path):
    window._add_attachments(make_files(tmp_path, "a" * 40 + ".pdf"))
    window.root.update()
    assert chip_labels(window) == ["PDF " + "a" * 27 + "… ✕"]


def test_remove_a_chip(window, tmp_path):
    a, b = make_files(tmp_path, "a.txt", "b.txt")
    window._add_attachments([a, b])
    window.root.update()
    window.chip_row.winfo_children()[0].winfo_children()[2].event_generate("<Button-1>")
    window.root.update()
    assert window.attachments == [b]


def test_chip_hover_shows_the_full_path(window, tmp_path):
    from nova.interfaces.gui import CHIP_BORDER, CHIP_HOVER, FIELD, MUTED

    (pdf,) = make_files(tmp_path, "report.pdf")
    window._add_attachments([pdf])
    window.root.update()
    chip = window.chip_row.winfo_children()[0]
    name = chip.winfo_children()[1]
    name.event_generate("<Enter>")
    window.root.update()
    assert window.status.cget("text") == f"{pdf} (PDF document, 1 B)"
    assert chip.cget("bg") == CHIP_HOVER
    assert chip.cget("highlightbackground") == MUTED  # a clearly brighter edge
    name.event_generate("<Leave>")  # the real pointer is elsewhere, so this leaves the chip
    window.root.update()
    assert window.status.cget("text") == "Ready"
    assert chip.cget("bg") == FIELD
    assert chip.cget("highlightbackground") == CHIP_BORDER


def test_send_composes_the_message_and_clears(window, tmp_path):
    (pdf,) = make_files(tmp_path, "report.pdf")
    window._add_attachments([pdf])
    window.entry.insert("1.0", "summarise it")
    window._submit()
    window.worker.join(5)
    window.root.update()
    assert window.sent[0].startswith("summarise it\n\nAttached files")
    assert f"- {pdf} (PDF document, 1 B)" in window.sent[0]
    assert window.attachments == []
    assert not window.chip_row.winfo_ismapped()
    assert "Attached: report.pdf" in window.transcript.get("1.0", "end")


def test_send_with_only_attachments(window, tmp_path):
    window._add_attachments(make_files(tmp_path, "a.png"))
    window._submit()
    window.worker.join(5)
    assert window.sent[0].startswith("Take a look at the attached files.")


def test_nothing_to_send(window):
    window._submit()
    assert window.worker is None and window.sent == []


def test_attachments_wait_while_busy(window, tmp_path):
    window.busy = True
    window._add_attachments(make_files(tmp_path, "later.txt"))
    window._submit()
    assert window.sent == [] and len(window.attachments) == 1


def test_cap_is_reported(window, tmp_path):
    from nova.attachments import MAX_ATTACHMENTS

    window._add_attachments(make_files(tmp_path, *[f"f{i}.txt" for i in range(MAX_ATTACHMENTS + 2)]))
    assert window.status.cget("text") == "Left out 2 files: at most 50 per message."


def test_adding_files_is_confirmed(window, tmp_path):
    a, b, c = make_files(tmp_path, "a.txt", "b.txt", "c.txt")
    window._add_attachments([a])
    assert window.status.cget("text") == "Attached a.txt"
    window._add_attachments([b, c])
    assert window.status.cget("text") == "Attached 2 files"


def test_attach_button_explains_the_other_ways(window):
    window.attach_button.event_generate("<Enter>")
    window.root.update()
    hint = window.status.cget("text")
    assert "⌘O" in hint and "drag" in hint and "⌘V" in hint
    window.attach_button.event_generate("<Leave>")
    window.root.update()
    assert window.status.cget("text") == "Ready"


def test_attach_shortcut_opens_the_picker(window, tmp_path, monkeypatch):
    from tkinter import filedialog

    (doc,) = make_files(tmp_path, "doc.txt")
    monkeypatch.setattr(filedialog, "askopenfilenames", lambda **_kw: (str(doc),))
    assert window.entry.bind("<Command-o>")  # the shortcut is wired to the message box
    assert window._choose_files() == "break"
    assert window.attachments == [doc]


def test_backspace_in_an_empty_box_removes_the_last_attachment(window, tmp_path):
    a, b = make_files(tmp_path, "a.txt", "b.txt")
    window._add_attachments([a, b])
    assert window._on_backspace() == "break"
    assert window.attachments == [a]
    window.entry.insert("1.0", "typing")
    assert window._on_backspace() is None  # normal Backspace while there is text
    assert window.attachments == [a]


# --- paste and drag and drop -------------------------------------------------
def put_image_on_clipboard(path):
    subprocess.run(
        ["osascript", "-e", f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'],
        check=True,
    )


def put_text_on_clipboard(text):
    subprocess.run(["pbcopy"], input=text.encode(), check=True)


def test_paste_an_image(window, tmp_path):
    from PIL import Image

    png = tmp_path / "shot.png"
    Image.new("RGB", (20, 10), "green").save(png)
    put_image_on_clipboard(png)
    assert window._paste() == "break"
    (pasted,) = window.attachments
    assert pasted.parent == (tmp_path / "attachments").resolve()
    assert pasted.name.startswith("paste-")
    assert Image.open(pasted).size == (20, 10)
    assert window.status.cget("text") == "Attached the pasted screenshot"


def test_paste_text_is_left_alone(window):
    put_text_on_clipboard("just words")
    assert window._paste() is None
    assert window.attachments == []


def test_command_v_is_bound(window):
    assert window.entry.bind("<Command-v>")


def test_drop_files_with_spaces_and_accents(window, tmp_path):
    folder = tmp_path / "My Files"
    folder.mkdir()
    doc = folder / "résumé final.pdf"
    doc.write_bytes(b"x")
    png = tmp_path / "b.png"
    png.write_bytes(b"x")
    event = types.SimpleNamespace(data=f"{{{doc}}} {png} https://example.com", action="copy")
    assert window._on_drop(event) == "copy"
    assert window.attachments == [doc.resolve(), png.resolve()]
    assert window.status.cget("text") == "Attached 2 files"


def test_window_accepts_drops(window):
    assert window.can_drop, "tkinterdnd2 should be installed and load"


def drag_feedback_on(win):
    from nova.interfaces.gui import ACCENT, FIELD

    return (
        win.status.cget("text") == "Drop to attach"
        and win.transcript.cget("bg") == FIELD
        and win.entry.cget("highlightbackground") == ACCENT
    )


def settle(win, seconds=0.2):
    """Let pending after() callbacks - the delayed drag-leave - run."""
    end = time.time() + seconds
    while time.time() < end:
        win.root.update()
        time.sleep(0.01)


def test_dragging_over_the_window_highlights_it(window):
    from nova.interfaces.gui import BG

    ring = window.entry.cget("highlightbackground")
    event = types.SimpleNamespace(action="copy")
    window._on_drag_enter(event)
    assert drag_feedback_on(window)
    window._on_drag_leave(event)
    settle(window)
    assert window.status.cget("text") == "Ready"
    assert window.transcript.cget("bg") == BG
    assert window.entry.cget("highlightbackground") == ring  # the original ring is back


def test_moving_between_parts_of_the_window_does_not_flicker(window):
    event = types.SimpleNamespace(action="copy")
    window._on_drag_enter(event)
    window._on_drag_leave(event)  # left the conversation...
    window._on_drag_enter(event)  # ...straight into the message box
    settle(window)
    assert drag_feedback_on(window)


def test_dropping_ends_the_highlight(window, tmp_path):
    from nova.interfaces.gui import BG

    (doc,) = make_files(tmp_path, "doc.txt")
    window._on_drag_enter(types.SimpleNamespace(action="copy"))
    window._on_drop(types.SimpleNamespace(data=str(doc), action="copy"))
    assert window.transcript.cget("bg") == BG
    assert window.status.cget("text") == "Attached doc.txt"


def test_two_leaves_in_a_row_still_cancel_cleanly(window):
    event = types.SimpleNamespace(action="copy")
    window._on_drag_enter(event)
    window._on_drag_leave(event)
    window._on_drag_leave(event)  # a second leave must not strand the first reset
    window._on_drag_enter(event)
    settle(window)
    assert drag_feedback_on(window)


def _contrast(a, b):
    def lum(h):
        r, g, bl = (int(h.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_attach_label_is_readable_on_macos_buttons(window):
    # macOS ignores a tk.Button's bg and draws a white face in an active window.
    assert _contrast(window.attach_button.cget("fg"), "#ffffff") >= 4.5


def tap_backspace(win):
    """A Backspace pressed and let go, plus what Tk's own binding does with the
    press if Nova's handler lets it through: delete a character."""
    result = win._on_backspace()
    if result is None and win.entry.get("1.0", "end-1c"):
        win.entry.delete("end-2c")
    win._on_backspace_release()
    settle(win, 0.1)
    return result


def test_holding_backspace_does_not_strip_attachments(window, tmp_path):
    files = make_files(tmp_path, "a.txt", "b.txt", "c.txt")
    window._add_attachments(files)
    window.entry.insert("1.0", "x")
    window._on_backspace()  # pressed: deletes the text
    window.entry.delete("end-2c")
    window._on_backspace()  # held: repeats as further presses (macOS, Windows)
    window._on_backspace()
    window._on_backspace_release()  # or as release-and-press pairs (X11)
    window._on_backspace()
    assert window.attachments == files
    window._on_backspace_release()  # finally let go
    settle(window, 0.1)
    assert window.attachments == files


def test_a_fresh_backspace_removes_the_last_attachment_and_says_so(window, tmp_path):
    a, b = make_files(tmp_path, "a.txt", "b.txt")
    window._add_attachments([a, b])
    assert tap_backspace(window) == "break"
    assert window.attachments == [a]
    assert window.status.cget("text") == "Removed b.txt"
    tap_backspace(window)
    assert window.attachments == []


# --- clipboards built the way macOS apps build them --------------------------
def png_bytes():
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


def set_pasteboard(*types_and_values):
    """Replace the clipboard with one item offering these (type, value) pairs,
    in this order of preference - the way the copying app would."""
    from AppKit import NSPasteboard, NSPasteboardItem
    from Foundation import NSData

    item = NSPasteboardItem.alloc().init()
    for uti, value in types_and_values:
        if isinstance(value, str):
            item.setString_forType_(value, uti)
        else:
            item.setData_forType_(NSData.dataWithBytes_length_(value, len(value)), uti)
    board = NSPasteboard.generalPasteboard()
    board.clearContents()
    board.writeObjects_([item])


def test_paste_a_file_copied_in_finder_attaches_the_file(window, tmp_path):
    from Foundation import NSURL

    (doc,) = make_files(tmp_path, "report.pdf")
    set_pasteboard(  # Finder offers the file, its name, and a picture of its icon
        ("public.file-url", NSURL.fileURLWithPath_(str(doc)).absoluteString()),
        ("public.utf8-plain-text", "report.pdf"),
        ("public.png", png_bytes()),
    )
    assert window._paste() == "break"
    assert window.attachments == [doc]


def test_paste_spreadsheet_cells_pastes_their_text(window):
    set_pasteboard(  # Numbers and Excel offer the text first, then a picture of it
        ("public.utf8-plain-text", "Item\tPrice\nPen\t2"),
        ("public.png", png_bytes()),
    )
    assert window._paste() is None  # normal text paste goes ahead
    assert window.attachments == []


def test_paste_a_real_screenshot(window):
    subprocess.run(["screencapture", "-c", "-x", "-R0,0,10,10"], check=True)  # a 10x10 corner
    assert window._paste() == "break"
    assert len(window.attachments) == 1
    assert window.status.cget("text") == "Attached the pasted screenshot"
