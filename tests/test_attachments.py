import os

import pytest
from PIL import Image

from nova import attachments
from nova.attachments import (
    DEFAULT_PROMPT,
    MAX_ATTACHMENTS,
    add_paths,
    badge,
    compose_message,
    describe,
    format_size,
    kind_of,
    save_pasted_image,
    summary_line,
)


@pytest.mark.parametrize(
    "size, expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (2048, "2.0 KB"),
        (int(2.1 * 1024**2), "2.1 MB"),
        (5 * 1024**3, "5.0 GB"),
        (3000 * 1024**3, "3000.0 GB"),
    ],
)
def test_format_size(size, expected):
    assert format_size(size) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("photo.HEIC", "image"),
        ("shot.png", "image"),
        ("scan.JPG", "image"),
        ("report.pdf", "PDF document"),
        ("REPORT.PDF", "PDF document"),
        ("letter.DOCX", "Word document"),
        ("old.doc", "Word document"),
        ("book.xlsx", "Excel workbook"),
        ("deck.pptx", "PowerPoint deck"),
        ("notes.rtf", "document"),
        ("data.csv", "file"),
        ("Makefile", "file"),
    ],
)
def test_kind_of_files(tmp_path, name, expected):
    path = tmp_path / name
    path.write_bytes(b"x")
    assert kind_of(path) == expected


def test_kind_of_folder(tmp_path):
    assert kind_of(tmp_path) == "folder"


def test_compose_message_lists_each_attachment(tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"x" * 2048)
    folder = tmp_path / "receipts"
    folder.mkdir()
    assert compose_message("  summarise it  ", [pdf, folder]) == (
        "summarise it\n\n"
        "Attached files (open them with read_file, or view_image for pictures):\n"
        f"- {pdf} (PDF document, 2.0 KB)\n"
        f"- {folder}/ (folder)"
    )


def test_compose_message_without_text_uses_default_prompt(tmp_path):
    photo = tmp_path / "a.png"
    photo.write_bytes(b"x")
    assert compose_message("", [photo]).startswith(DEFAULT_PROMPT + "\n\n")


def test_compose_message_without_attachments_is_just_the_text():
    assert compose_message(" hello ", []) == "hello"


def test_compose_message_survives_a_file_deleted_after_attaching(tmp_path):
    gone = tmp_path / "gone.pdf"  # never created
    assert f"- {gone} (PDF document)" in compose_message("x", [gone])


def test_add_paths_keeps_existing_files_and_ignores_the_rest(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("a")
    b = tmp_path / "b c é.txt"
    b.write_text("b")
    paths, left_out = add_paths(
        [],
        [str(a), b, str(a), "", "   ", "https://example.com/x", str(tmp_path / "missing.txt")],
    )
    assert paths == [a.resolve(), b.resolve()]
    assert left_out == 0


def test_add_paths_caps_the_count(tmp_path):
    names = []
    for i in range(MAX_ATTACHMENTS + 3):
        path = tmp_path / f"f{i}.txt"
        path.write_text("x")
        names.append(path)
    paths, left_out = add_paths([], names)
    assert len(paths) == MAX_ATTACHMENTS
    assert left_out == 3


def test_add_paths_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "doc.txt").write_text("x")
    assert add_paths([], ["~/doc.txt"])[0] == [(tmp_path / "doc.txt").resolve()]


def test_save_pasted_image_names_and_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments.time, "strftime", lambda fmt: "paste-20261013-101500")
    image = Image.new("RGB", (10, 10), "blue")
    first = save_pasted_image(image, tmp_path)
    second = save_pasted_image(image, tmp_path)
    assert first.name == "paste-20261013-101500.png"
    assert second.name == "paste-20261013-101500-2.png"
    assert Image.open(second).size == (10, 10)


def test_save_pasted_image_defaults_to_the_attachments_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "ATTACHMENTS_DIR", tmp_path / "attachments")
    path = save_pasted_image(Image.new("RGB", (4, 4)))
    assert path.parent == tmp_path / "attachments"


def test_save_pasted_image_keeps_only_the_newest(tmp_path):
    for i in range(attachments.KEEP_PASTES + 5):
        old = tmp_path / f"paste-old-{i:03d}.png"
        old.write_bytes(b"x")
        os.utime(old, ns=(i * 10**9, i * 10**9))
    not_a_paste = tmp_path / "notes.png"
    not_a_paste.write_bytes(b"x")
    new = save_pasted_image(Image.new("RGB", (4, 4)), tmp_path)
    pastes = sorted(p.name for p in tmp_path.glob("paste-*.png"))
    assert len(pastes) == attachments.KEEP_PASTES
    assert new.name in pastes
    assert "paste-old-000.png" not in pastes and "paste-old-005.png" not in pastes
    assert "paste-old-006.png" in pastes
    assert not_a_paste.exists()


# --- text the window shows for attachments ---------------------------------
@pytest.mark.parametrize(
    "name, expected",
    [
        ("report.pdf", "PDF"),
        ("IMG_2041.HEIC", "HEIC"),
        ("deck.pptx", "PPTX"),
        ("photo.jpeg", "JPEG"),
        ("notes.markdown", "FILE"),  # too long to be a useful tag
        ("Makefile", "FILE"),
    ],
)
def test_badge_names_the_file_type(tmp_path, name, expected):
    path = tmp_path / name
    path.write_bytes(b"x")
    assert badge(path) == expected


def test_badge_for_a_folder(tmp_path):
    assert badge(tmp_path) == "FOLDER"


def test_describe_gives_path_kind_and_size(tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"x" * 2048)
    assert describe(pdf) == f"{pdf} (PDF document, 2.0 KB)"
    assert describe(tmp_path) == f"{tmp_path}/ (folder)"


def test_summary_line_names_each_file(tmp_path):
    assert summary_line([tmp_path / "a.pdf", tmp_path / "b.png"]) == "Attached: a.pdf, b.png"


def test_summary_line_marks_folders(tmp_path):
    folder = tmp_path / "receipts"
    folder.mkdir()
    assert summary_line([folder]) == "Attached: receipts/"


def test_summary_line_collapses_long_lists(tmp_path):
    paths = [tmp_path / f"f{i}.txt" for i in range(9)]
    assert summary_line(paths) == (
        "Attached: f0.txt, f1.txt, f2.txt, f3.txt, f4.txt, f5.txt and 3 more"
    )
