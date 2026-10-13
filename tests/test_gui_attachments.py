"""GUI checks for attachments.

They open a real Nova window and, for pasting, use the real clipboard - which
they overwrite - so they only run when asked:

    NOVA_GUI_TESTS=1 .venv/bin/python -m pytest tests/test_gui_attachments.py -q
"""
import os
import sys

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
