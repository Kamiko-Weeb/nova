# File Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner attach files to a message in Nova's desktop window (📎 button, drag and drop, pasted screenshot) and have Nova read PDF, Word, Excel and PowerPoint files and look at pictures.

**Architecture:** Attachments travel as a list of paths appended to the owner's message; the model opens them itself. `read_file` gains text extraction for document formats (`nova/tools/documents.py`), a new `view_image` tool sends picture files to the vision model, and the tkinter window gets the button, chips, paste and drop. Pure, UI-free helpers live in `nova/attachments.py`.

**Tech Stack:** Python 3.12+, tkinter, Pillow, pypdf, python-docx, openpyxl, python-pptx, pillow-heif, tkinterdnd2, pytest, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-10-13-file-attachments-design.md`

## Global Constraints

- Branch: `attachments`. Run everything from the repo root, `/Users/victor/Desktop/nova`, with the venv: `.venv/bin/python`.
- Python 3.12 or newer (CI uses 3.12; the code already relies on `Path.walk`).
- Message sent to the agent, exactly: typed text, or `Take a look at the attached files.` when nothing was typed, then a blank line, then `Attached files (open them with read_file, or view_image for pictures):`, then one `- <path> (<kind>, <size>)` line per file; folders are `- <path>/ (folder)`.
- Kinds: `image` (png, jpg, jpeg, heic, heif, webp, gif, tiff, tif, bmp), `PDF document` (pdf), `Word document` (docx, doc), `Excel workbook` (xlsx, xlsm), `PowerPoint deck` (pptx), `document` (rtf, odt), `folder`, otherwise `file`. Extensions compare case-insensitively.
- Sizes: bytes below 1024 as `512 B`, otherwise one decimal in KB, MB or GB (`2.1 MB`).
- Limits: 50 attachments per message, 6 chips shown before `+N more`, 50 pasted images kept, 5,000 rows per spreadsheet sheet.
- Pasted images: `paste-YYYYMMDD-HHMMSS.png`, then `-2`, `-3` on a clash, in `DATA_ROOT / "attachments"` (gitignored `attachments/` in a checkout, `~/.nova/attachments` when packaged).
- Every new library is imported lazily; a missing one only disables its format, with a message naming the package. Without tkinterdnd2 the window is a plain `tk.Tk` with no drag and drop.
- `view_image` is read-only: never `dangerous=True`.
- Lint must stay clean: `uvx pyflakes nova main.py packaging/nova_app.py tests`.
- Commit messages end with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never push or merge to `main` from this plan: a push to `main` publishes a release.

## Review Focus

- Uppercase extensions from cameras and phones (`IMG_0001.HEIC`, `SCAN.JPG`, `REPORT.PDF`) must behave exactly like lowercase ones. Pinned in Tasks 1, 2, 3 and 4.
- Dropped paths with spaces, accents or Tk's `{braces}` quoting (`{/Users/v/My Files/résumé final.pdf}`) must attach the right file; dragged links and text must be ignored. Pinned in Tasks 1 and 6.
- A file deleted or moved after it was attached must not break sending: the list line loses its size and Nova later gets "does not exist". Pinned in Task 1.
- Cmd+V of ordinary text must still paste text. Pinned in Task 6.
- Corrupt or misnamed files (a `.pdf` that is really text, a `.png` that is not an image) must come back as a one-line error, never a traceback. Pinned in Tasks 2 and 4.

---

### Task 1: Attachment helpers and the test suite

**Files:**
- Create: `nova/attachments.py`
- Create: `tests/conftest.py`
- Create: `tests/test_attachments.py`
- Modify: `.gitignore` (append)

**Interfaces:**
- Consumes: `nova.config.DATA_ROOT`, `nova.config.Config`.
- Produces (later tasks import these exact names from `nova.attachments`):
  - `ATTACHMENTS_DIR: Path`, `MAX_ATTACHMENTS = 50`, `KEEP_PASTES = 50`
  - `DEFAULT_PROMPT = "Take a look at the attached files."`
  - `IMAGE_SUFFIXES: frozenset[str]`, `DOCUMENT_KINDS: dict[str, str]` (suffix → kind label)
  - `format_size(size: float) -> str`
  - `kind_of(path: Path) -> str`
  - `compose_message(text: str, paths: Sequence[Path]) -> str`
  - `add_paths(current: list[Path], candidates: Iterable[str | Path]) -> tuple[list[Path], int]` (new list, number left out by the cap)
  - `save_pasted_image(image, folder: Path | None = None) -> Path`
  - pytest fixture `cfg` (a `Config` whose workspace is `tmp_path`)

- [ ] **Step 1: Install pytest into the venv**

Run: `uv pip install --python .venv/bin/python pytest`
Expected: `+ pytest==...` (or "Audited" if already present).

- [ ] **Step 2: Write the shared fixture file**

Create `tests/conftest.py`:

```python
"""Shared fixtures. Sample files are built fresh in each test's temp folder."""
from __future__ import annotations

import dataclasses

import pytest

from nova.config import Config


@pytest.fixture
def cfg(tmp_path):
    """Nova's config with the workspace pointed at the test's temp folder."""
    return dataclasses.replace(
        Config.from_env(),
        workspace=tmp_path,
        restrict_to_workspace=False,
        log_dir=tmp_path / "logs",
    )
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_attachments.py`:

```python
import os

import pytest
from PIL import Image

from nova import attachments
from nova.attachments import (
    DEFAULT_PROMPT,
    MAX_ATTACHMENTS,
    add_paths,
    compose_message,
    format_size,
    kind_of,
    save_pasted_image,
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
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `.venv/bin/python -m pytest tests/test_attachments.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'nova.attachments'`.

- [ ] **Step 5: Write `nova/attachments.py`**

```python
"""Files attached to a message in the desktop window.

UI-free helpers: what kind of file each attachment is, the list of them that is
appended to the owner's message, and saving a pasted screenshot. Nova opens
attachments itself, with read_file or view_image, so only their paths travel
with the message.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Sequence

from .config import DATA_ROOT

ATTACHMENTS_DIR = DATA_ROOT / "attachments"
MAX_ATTACHMENTS = 50
KEEP_PASTES = 50
DEFAULT_PROMPT = "Take a look at the attached files."
HEADER = "Attached files (open them with read_file, or view_image for pictures):"

IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp", ".gif", ".tiff", ".tif", ".bmp"}
)
# Formats read_file extracts text from, and how each is named to the model.
DOCUMENT_KINDS = {
    ".pdf": "PDF document",
    ".docx": "Word document",
    ".doc": "Word document",
    ".xlsx": "Excel workbook",
    ".xlsm": "Excel workbook",
    ".pptx": "PowerPoint deck",
    ".rtf": "document",
    ".odt": "document",
}


def format_size(size: float) -> str:
    """Bytes for a person: '512 B', '2.0 KB', '2.1 MB'."""
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
    raise AssertionError("unreachable")


def kind_of(path: Path) -> str:
    if path.is_dir():
        return "folder"
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return DOCUMENT_KINDS.get(suffix, "file")


def _listing(path: Path) -> str:
    kind = kind_of(path)
    if kind == "folder":
        return f"- {path}/ (folder)"
    try:
        size = format_size(path.stat().st_size)
    except OSError:  # deleted or moved since it was attached
        return f"- {path} ({kind})"
    return f"- {path} ({kind}, {size})"


def compose_message(text: str, paths: Sequence[Path]) -> str:
    """The instruction for the agent: what was typed, then the attachments."""
    text = text.strip()
    if not paths:
        return text
    return "\n".join([text or DEFAULT_PROMPT, "", HEADER, *map(_listing, paths)])


def add_paths(
    current: list[Path], candidates: Iterable[str | Path]
) -> tuple[list[Path], int]:
    """`current` plus every candidate that is an existing file or folder.

    Duplicates are skipped and the total is capped at MAX_ATTACHMENTS. Anything
    that is not an existing path - dragged text, a link - is ignored. Returns
    the new list and how many were left out because of the cap.
    """
    result = list(current)
    seen = set(result)
    left_out = 0
    for candidate in candidates:
        if not str(candidate).strip():
            continue
        try:
            path = Path(candidate).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if path in seen or not path.exists():
            continue
        if len(result) >= MAX_ATTACHMENTS:
            left_out += 1
            continue
        result.append(path)
        seen.add(path)
    return result, left_out


def save_pasted_image(image, folder: Path | None = None) -> Path:
    """Save a pasted image as paste-<time>.png, keeping only the newest
    KEEP_PASTES pastes in the folder."""
    folder = folder or ATTACHMENTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("paste-%Y%m%d-%H%M%S")
    path, number = folder / f"{stem}.png", 1
    while path.exists():
        number += 1
        path = folder / f"{stem}-{number}.png"
    image.save(path, format="PNG")
    pastes = sorted(
        folder.glob("paste-*.png"), key=lambda p: (p.stat().st_mtime_ns, p.name)
    )
    for old in pastes[:-KEEP_PASTES]:
        old.unlink(missing_ok=True)
    return path
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `.venv/bin/python -m pytest tests/test_attachments.py -q`
Expected: all pass (about 30 tests).

- [ ] **Step 7: Ignore the attachments folder**

Append to `.gitignore`:

```
# Pasted screenshots attached in the window
attachments/
```

- [ ] **Step 8: Lint and commit**

Run: `uvx pyflakes nova main.py packaging/nova_app.py tests`
Expected: no output.

```bash
git add nova/attachments.py tests/conftest.py tests/test_attachments.py .gitignore
git commit -m "Add attachment helpers and a pytest suite

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Extracting text from documents

**Files:**
- Create: `nova/tools/documents.py`
- Create: `tests/test_documents.py`
- Modify: `tests/conftest.py` (append fixtures)
- Modify: `requirements.txt` (append)

**Interfaces:**
- Consumes: `nova.attachments.DOCUMENT_KINDS`.
- Produces (in `nova.tools.documents`):
  - `class DocumentError(Exception)` — message is fit to show the model
  - `@dataclass(frozen=True) class Extracted: text: str; summary: str` — summary such as `"PDF, 2 pages"`
  - `is_document(path: Path) -> bool`
  - `extract_text(path: Path) -> Extracted` — raises `DocumentError`, or `PermissionError` when the OS refuses access
  - `MAX_SHEET_ROWS = 5_000`, `_READERS: dict[str, Callable[[Path], Extracted]]`, `_extract` (an `lru_cache`, exposed for tests)
  - fixtures `make_pdf(name, pages, password=None) -> Path`, `scan_pdf`, `sample_docx`, `sample_xlsx`, `sample_pptx`

- [ ] **Step 1: Add and install the document libraries**

Append to `requirements.txt`:

```
# Reading attached documents (each optional: without one, only that format fails)
pypdf>=5.0
python-docx>=1.1
openpyxl>=3.1
python-pptx>=1.0
```

Run: `uv pip install --python .venv/bin/python -r requirements.txt`
Expected: pypdf, python-docx, openpyxl, python-pptx (and lxml, et-xmlfile, xlsxwriter) installed.

- [ ] **Step 2: Add sample-document fixtures**

Append to `tests/conftest.py`:

```python
import io
from pathlib import Path


def _pdf_bytes(pages: list[str]) -> bytes:
    """A minimal, valid PDF with one line of Helvetica text per page."""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "",  # the page tree, filled in once the pages exist
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in pages:
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {len(objects)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


@pytest.fixture
def make_pdf(tmp_path):
    def make(name: str, pages: list[str], password: str | None = None) -> Path:
        path = tmp_path / name
        path.write_bytes(_pdf_bytes(pages))
        if password is not None:
            from pypdf import PdfReader, PdfWriter

            writer = PdfWriter(clone_from=PdfReader(io.BytesIO(path.read_bytes())))
            writer.encrypt(user_password=password, owner_password="owner", algorithm="RC4-128")
            writer.write(path)
        return path

    return make


@pytest.fixture
def scan_pdf(tmp_path):
    """A PDF that is only a picture, like a scan: no text layer."""
    from PIL import Image

    path = tmp_path / "scan.pdf"
    Image.new("RGB", (200, 100), "white").save(path)
    return path


@pytest.fixture
def sample_docx(tmp_path):
    import docx

    document = docx.Document()
    document.add_paragraph("Intro paragraph")
    table = document.add_table(rows=2, cols=2)
    for (row, col), text in {(0, 0): "Name", (0, 1): "Qty", (1, 0): "Apples", (1, 1): "3"}.items():
        table.cell(row, col).text = text
    document.add_paragraph("Closing paragraph")
    path = tmp_path / "report.docx"
    document.save(path)
    return path


@pytest.fixture
def sample_xlsx(tmp_path):
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Totals"
    sheet.append(["Item", "Price", None])
    sheet.append(["Pen", 2, None])
    sheet.append([None, None, None])
    sheet["B4"] = "=SUM(B2:B2)"  # never calculated, so no value: must not show as text
    workbook.create_sheet("Empty")
    path = tmp_path / "book.xlsx"
    workbook.save(path)
    return path


@pytest.fixture
def sample_pptx(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])  # title only
    slide.shapes.title.text = "Quarterly review"
    group = slide.shapes.add_group_shape()
    box = group.shapes.add_textbox(Inches(1), Inches(2), Inches(3), Inches(1))
    box.text_frame.text = "Grouped note"
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(4), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "Region"
    table.cell(0, 1).text = "North"
    slide.notes_slide.notes_text_frame.text = "Mention the north"
    deck.slides.add_slide(deck.slide_layouts[6])  # a blank second slide
    path = tmp_path / "deck.pptx"
    deck.save(path)
    return path


@pytest.fixture(autouse=True)
def _fresh_document_cache():
    from nova.tools import documents

    documents._extract.cache_clear()
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_documents.py`:

```python
import builtins
import sys

import pytest

from nova.attachments import DOCUMENT_KINDS
from nova.tools import documents
from nova.tools.documents import DocumentError, extract_text


def test_every_document_kind_has_a_reader():
    assert set(documents._READERS) == set(DOCUMENT_KINDS)


def test_pdf_pages(make_pdf):
    result = extract_text(make_pdf("report.pdf", ["Hello page one", "Total 4182 dollars"]))
    assert result.summary == "PDF, 2 pages"
    assert result.text == "--- page 1 ---\nHello page one\n--- page 2 ---\nTotal 4182 dollars"


def test_pdf_uppercase_extension(make_pdf):
    assert "Shout" in extract_text(make_pdf("LOUD.PDF", ["Shout"])).text


def test_pdf_with_empty_password_opens(make_pdf):
    assert "Opened" in extract_text(make_pdf("open.pdf", ["Opened"], password="")).text


def test_pdf_with_real_password_is_reported(make_pdf):
    with pytest.raises(DocumentError, match="locked.pdf is password-protected"):
        extract_text(make_pdf("locked.pdf", ["Secret"], password="hunter2"))


def test_scanned_pdf_is_reported(scan_pdf):
    with pytest.raises(DocumentError, match="no text found in scan.pdf; it looks like a scan"):
        extract_text(scan_pdf)


def test_corrupt_pdf_is_reported_not_raised(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_text("this is not a pdf")
    with pytest.raises(DocumentError, match="could not read fake.pdf"):
        extract_text(fake)


def test_docx_in_document_order(sample_docx):
    result = extract_text(sample_docx)
    assert result.summary == "Word document"
    assert result.text == "Intro paragraph\nName | Qty\nApples | 3\nClosing paragraph"


def test_xlsx_values_not_formulas(sample_xlsx):
    result = extract_text(sample_xlsx)
    assert result.summary == "Excel workbook, 2 sheets"
    assert result.text == "=== Sheet: Totals ===\nItem | Price\nPen | 2\n=== Sheet: Empty ==="


def test_xlsx_row_cap(tmp_path, monkeypatch):
    import openpyxl

    monkeypatch.setattr(documents, "MAX_SHEET_ROWS", 3)
    workbook = openpyxl.Workbook()
    for i in range(5):
        workbook.active.append([f"row {i}"])
    path = tmp_path / "big.xlsx"
    workbook.save(path)
    lines = extract_text(path).text.splitlines()
    assert lines[1:4] == ["row 0", "row 1", "row 2"]
    assert lines[4] == "... stopped at 3 rows; the sheet has more"
    assert len(lines) == 5


def test_pptx_slides_groups_tables_notes(sample_pptx):
    result = extract_text(sample_pptx)
    assert result.summary == "PowerPoint deck, 2 slides"
    assert result.text == (
        "--- slide 1 ---\nQuarterly review\nGrouped note\nRegion | North\n"
        "Notes: Mention the north\n--- slide 2 ---"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="textutil is macOS only")
def test_rtf_through_textutil(tmp_path):
    rtf = tmp_path / "note.rtf"
    rtf.write_text(r"{\rtf1\ansi Hello from RTF}")
    result = extract_text(rtf)
    assert "Hello from RTF" in result.text
    assert result.summary == "document"


@pytest.mark.skipif(sys.platform == "darwin", reason="checks the non-Mac message")
def test_rtf_elsewhere_explains(tmp_path):
    rtf = tmp_path / "note.rtf"
    rtf.write_text(r"{\rtf1 x}")
    with pytest.raises(DocumentError, match="needs macOS"):
        extract_text(rtf)


def test_missing_library_is_named(monkeypatch, sample_docx):
    real_import = builtins.__import__

    def without_docx(name, *args, **kwargs):
        if name == "docx":
            raise ImportError("no docx")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_docx)
    with pytest.raises(DocumentError, match=r"reading \.docx needs the python-docx package"):
        extract_text(sample_docx)


def test_extraction_is_cached_until_the_file_changes(make_pdf):
    path = make_pdf("c.pdf", ["one"])
    extract_text(path)
    extract_text(path)
    assert documents._extract.cache_info().hits == 1
    path.write_bytes(path.read_bytes() + b"\n")  # new size, so a new cache key
    extract_text(path)
    assert documents._extract.cache_info().misses == 2
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `.venv/bin/python -m pytest tests/test_documents.py -q`
Expected: errors, `ModuleNotFoundError: No module named 'nova.tools.documents'`.

- [ ] **Step 5: Write `nova/tools/documents.py`**

```python
"""Reading documents that are not plain text.

extract_text turns PDF, Word, Excel and PowerPoint files - and on macOS the
older formats textutil understands - into text for read_file, which numbers and
pages it like any other file. Each format's library is imported only when that
format is read, so a missing one fails just that format, with a message saying
what to install.
"""
from __future__ import annotations

import functools
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..attachments import DOCUMENT_KINDS

MAX_SHEET_ROWS = 5_000


class DocumentError(Exception):
    """A document could not be read; the message is fit to hand to the model."""


@dataclass(frozen=True)
class Extracted:
    text: str
    summary: str  # for read_file's header, e.g. "PDF, 12 pages"


def is_document(path: Path) -> bool:
    return path.suffix.lower() in DOCUMENT_KINDS


def extract_text(path: Path) -> Extracted:
    """The readable text of a document. Raises DocumentError, or
    PermissionError when the operating system refuses access."""
    stat = path.stat()
    return _extract(str(path), stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=4)
def _extract(path: str, _mtime_ns: int, _size: int) -> Extracted:
    # The modification time and size are only there to turn an edited file into
    # a cache miss, so paging through a long PDF reads it once.
    target = Path(path)
    suffix = target.suffix.lower()
    try:
        return _READERS[suffix](target)
    except (DocumentError, PermissionError):
        raise
    except ImportError as exc:
        raise DocumentError(
            f"reading {suffix} needs the {_PACKAGES.get(suffix, 'right')} package."
        ) from exc
    except Exception as exc:
        raise DocumentError(f"could not read {target.name}: {exc}") from exc


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _pdf(path: Path) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(path)
    # Many "protected" PDFs only restrict printing and open with no password.
    if reader.is_encrypted and not reader.decrypt(""):
        raise DocumentError(f"{path.name} is password-protected.")
    pages = [page.extract_text() or "" for page in reader.pages]
    if not any(text.strip() for text in pages):
        raise DocumentError(
            f"no text found in {path.name}; it looks like a scan, which read_file "
            "cannot read."
        )
    body = "\n".join(f"--- page {n} ---\n{text.strip()}" for n, text in enumerate(pages, 1))
    return Extracted(body, f"PDF, {_plural(len(pages), 'page')}")


def _docx(path: Path) -> Extracted:
    import docx
    from docx.table import Table

    lines = []
    for block in docx.Document(str(path)).iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        else:
            lines.append(block.text)
    return Extracted("\n".join(lines), "Word document")


def _xlsx(path: Path) -> Extracted:
    import openpyxl

    # data_only: the values Excel last calculated, not the formulas behind them.
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        lines = []
        for sheet in workbook.worksheets:
            lines.append(f"=== Sheet: {sheet.title} ===")
            rows = 0
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if value is None else str(value) for value in row]
                while cells and not cells[-1]:
                    cells.pop()
                if not cells:
                    continue
                if rows == MAX_SHEET_ROWS:
                    lines.append(f"... stopped at {MAX_SHEET_ROWS:,} rows; the sheet has more")
                    break
                lines.append(" | ".join(cells))
                rows += 1
        sheets = len(workbook.worksheets)
    finally:
        workbook.close()
    return Extracted("\n".join(lines), f"Excel workbook, {_plural(sheets, 'sheet')}")


def _pptx(path: Path) -> Extracted:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    def shape_text(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from shape_text(shape.shapes)
            elif shape.has_table:
                for row in shape.table.rows:
                    yield " | ".join(cell.text.strip() for cell in row.cells)
            elif shape.has_text_frame and shape.text_frame.text.strip():
                yield shape.text_frame.text.strip()

    deck = Presentation(str(path))
    lines = []
    for number, slide in enumerate(deck.slides, 1):
        lines.append(f"--- slide {number} ---")
        lines.extend(shape_text(slide.shapes))
        if slide.has_notes_slide:
            frame = slide.notes_slide.notes_text_frame
            notes = frame.text.strip() if frame is not None else ""
            if notes:
                lines.append(f"Notes: {notes}")
    return Extracted("\n".join(lines), f"PowerPoint deck, {_plural(len(deck.slides), 'slide')}")


def _textutil(path: Path) -> Extracted:
    """Older formats, through the converter built into macOS."""
    if sys.platform != "darwin":
        raise DocumentError(
            f"reading {path.suffix.lower()} files needs macOS; convert {path.name} "
            "to .docx first."
        )
    proc = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise DocumentError(
            f"could not read {path.name}: {proc.stderr.strip() or 'textutil failed'}"
        )
    return Extracted(proc.stdout, DOCUMENT_KINDS[path.suffix.lower()])


_READERS: dict[str, Callable[[Path], Extracted]] = {
    ".pdf": _pdf,
    ".docx": _docx,
    ".xlsx": _xlsx,
    ".xlsm": _xlsx,
    ".pptx": _pptx,
    ".doc": _textutil,
    ".rtf": _textutil,
    ".odt": _textutil,
}
_PACKAGES = {
    ".pdf": "pypdf",
    ".docx": "python-docx",
    ".xlsx": "openpyxl",
    ".xlsm": "openpyxl",
    ".pptx": "python-pptx",
}
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass; on macOS `test_rtf_elsewhere_explains` is skipped.

- [ ] **Step 7: Lint and commit**

Run: `uvx pyflakes nova main.py packaging/nova_app.py tests`
Expected: no output.

```bash
git add nova/tools/documents.py tests/test_documents.py tests/conftest.py requirements.txt
git commit -m "Extract text from PDF, Word, Excel and PowerPoint files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `read_file` reads documents, and points images and binaries elsewhere

**Files:**
- Modify: `nova/config.py` (add `Config.resolve_path`)
- Modify: `nova/tools/files.py:1-64` (imports, `resolve`, `read_file`)
- Create: `tests/test_read_file.py`

**Interfaces:**
- Consumes: `extract_text`, `is_document`, `DocumentError` from `nova.tools.documents`; `IMAGE_SUFFIXES`, `format_size` from `nova.attachments`.
- Produces: `Config.resolve_path(self, raw: str) -> Path` — absolute and resolved, relative paths taken from the workspace; raises `PermissionError` outside the workspace when `restrict_to_workspace` is on. (Task 4's `view_image` uses it.)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_read_file.py`:

```python
import os
import sys

import pytest

from nova.tools import files
from nova.tools.registry import Registry


@pytest.fixture
def read_file(cfg):
    registry = Registry()
    files.register(registry, cfg)
    return lambda **args: registry.invoke("read_file", args)


def test_pdf_through_read_file(read_file, make_pdf):
    path = make_pdf("report.pdf", ["Hello page one", "Total 4182 dollars"]).resolve()
    out = read_file(path=str(path))
    assert out.splitlines()[0] == f"{path}  (PDF, 2 pages, 4 lines total)"
    assert "     4\tTotal 4182 dollars" in out


def test_pages_through_a_long_document(read_file, make_pdf):
    path = make_pdf("long.pdf", [f"line {n}" for n in range(1, 31)])
    out = read_file(path=str(path), start_line=21, max_lines=2)
    assert out.splitlines()[1:] == ["    21\t--- page 11 ---", "    22\tline 11"]


def test_relative_paths_resolve_in_the_workspace(read_file, make_pdf):
    make_pdf("rel.pdf", ["relative ok"])  # created in tmp_path, which is the workspace
    assert "relative ok" in read_file(path="rel.pdf")


def test_document_errors_come_back_as_text(read_file, scan_pdf):
    assert read_file(path=str(scan_pdf)).startswith("ERROR: no text found in scan.pdf")


@pytest.mark.parametrize("name", ["photo.png", "IMG_0001.HEIC"])
def test_images_point_to_view_image(read_file, tmp_path, name):
    path = (tmp_path / name).resolve()
    path.write_bytes(b"\x89PNG not really")
    assert read_file(path=str(path)) == f"{path} is an image. Use view_image to look at it."


def test_binary_files_are_described_not_dumped(read_file, tmp_path):
    path = (tmp_path / "clip.mp4").resolve()
    path.write_bytes(b"\x00\x01binary" * 300)  # 2,400 bytes
    assert read_file(path=str(path)) == (
        f"{path}: binary file (.mp4, 2.3 KB) - read_file can only show text."
    )


def test_plain_text_is_unchanged(read_file, tmp_path):
    path = (tmp_path / "notes.txt").resolve()
    path.write_text("first\nsecond\n")
    assert read_file(path=str(path)) == f"{path}  (2 lines total)\n     1\tfirst\n     2\tsecond"


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0, reason="root can read anything")
def test_blocked_files_explain_where_to_allow_access(read_file, tmp_path):
    path = tmp_path / "secret.txt"
    path.write_text("x")
    path.chmod(0)
    try:
        out = read_file(path=str(path))
    finally:
        path.chmod(0o600)
    expected = "Files and Folders" if sys.platform == "darwin" else "permission denied"
    assert out.startswith("ERROR:") and expected in out


def test_workspace_restriction_still_applies(cfg, tmp_path):
    import dataclasses

    registry = Registry()
    files.register(registry, dataclasses.replace(cfg, restrict_to_workspace=True))
    assert "outside the allowed workspace" in registry.invoke("read_file", {"path": "/etc/hosts"})
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `.venv/bin/python -m pytest tests/test_read_file.py -q`
Expected: `test_pdf_through_read_file`, `test_pages_through_a_long_document`, `test_document_errors_come_back_as_text`, both image tests, the binary test and the blocked-file test FAIL (raw PDF bytes, no image or binary messages, a traceback-style error). The plain-text, relative-path (the raw PDF bytes happen to contain the words) and restriction tests already PASS.

- [ ] **Step 3: Move path resolution onto `Config`**

In `nova/config.py`, add this method to `class Config`, after `require_api_key`:

```python
    def resolve_path(self, raw: str) -> Path:
        """An absolute path for `raw`; relative paths start at the workspace.

        Raises PermissionError for a path outside the workspace while
        NOVA_RESTRICT_TO_WORKSPACE is on.
        """
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        if self.restrict_to_workspace:
            root = self.workspace.resolve()
            if root not in path.parents and path != root:
                raise PermissionError(
                    f"{path} is outside the allowed workspace ({root}). "
                    "Set NOVA_RESTRICT_TO_WORKSPACE=false to lift this."
                )
        return path
```

In `nova/tools/files.py`, replace the whole nested `def resolve(raw: str) -> Path: ...` block at the top of `register` (the 13 lines from `def resolve` through `return path`) with:

```python
    resolve = cfg.resolve_path
```

- [ ] **Step 4: Teach `read_file` the new cases**

In `nova/tools/files.py`, change the imports to:

```python
from ..attachments import IMAGE_SUFFIXES, format_size
from ..config import Config
from .documents import DocumentError, extract_text, is_document
from .registry import Registry, boolean, integer, schema, string, truncate
```

Add below `TRASH = ...`:

```python
def _blocked(path: Path) -> str:
    if sys.platform == "darwin":
        return (
            f"macOS blocked access to {path}. Allow it in System Settings > "
            "Privacy & Security > Files and Folders."
        )
    return f"permission denied reading {path}."
```

Replace the body of `read_file` (from `target = resolve(path)` through `return truncate(f"{header}\n{numbered}")`) with:

```python
        target = resolve(path)
        if not target.exists():
            return f"ERROR: {target} does not exist."
        if target.is_dir():
            return f"ERROR: {target} is a directory. Use list_dir."
        suffix = target.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            return f"{target} is an image. Use view_image to look at it."
        try:
            if is_document(target):
                extracted = extract_text(target)
                text, kind = extracted.text, f"{extracted.summary}, "
            else:
                raw = target.read_bytes()[:TEXT_READ_LIMIT]
                if b"\0" in raw[:8192]:
                    size = format_size(target.stat().st_size)
                    return (
                        f"{target}: binary file ({suffix or 'no extension'}, {size}) "
                        "- read_file can only show text."
                    )
                text, kind = raw.decode("utf-8", errors="replace"), ""
        except DocumentError as exc:
            return f"ERROR: {exc}"
        except PermissionError:
            return f"ERROR: {_blocked(target)}"
        lines = text.splitlines()
        begin = max(0, start_line - 1)
        chunk = lines[begin:] if max_lines <= 0 else lines[begin : begin + max_lines]
        numbered = "\n".join(
            f"{i:>6}\t{line}" for i, line in enumerate(chunk, start=begin + 1)
        )
        header = f"{target}  ({kind}{len(lines)} lines total)"
        return truncate(f"{header}\n{numbered}")
```

Update the `read_file` tool description string to:

```python
            "Read a file and return its text with line numbers. Also reads PDF, "
            "Word (.docx), Excel (.xlsx) and PowerPoint (.pptx) files, and on macOS "
            ".doc, .rtf and .odt. For pictures use view_image. Relative paths "
            "resolve against the workspace directory."
```

- [ ] **Step 5: Run the whole suite and the smoke test**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass.

Run the existing 17-step smoke test, which exercises every file tool:
`.venv/bin/python -I /private/tmp/claude-501/-Users-victor-Desktop/b78ea298-d31d-4283-a491-b047e855602e/scratchpad/smoke.py /Users/victor/Desktop/nova | tail -1`
Expected: `17/17 passed` (if the scratch file is gone, skip this step: the pytest suite covers the same paths).

- [ ] **Step 6: Lint and commit**

Run: `uvx pyflakes nova main.py packaging/nova_app.py tests`
Expected: no output.

```bash
git add nova/config.py nova/tools/files.py tests/test_read_file.py
git commit -m "read_file: read documents; point images and binaries elsewhere

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `view_image` for picture files

**Files:**
- Modify: `nova/tools/screen.py:23-49, 97` (public `fit_for_vision`)
- Modify: `nova/tools/documents.py` (add `open_image`, `DEFAULT_QUESTION`, `register`)
- Modify: `nova/tools/__init__.py` (register documents)
- Modify: `requirements.txt` (append)
- Modify: `tests/conftest.py` (append `make_image`)
- Create: `tests/test_view_image.py`

**Interfaces:**
- Consumes: `Config.resolve_path` (Task 3), `IMAGE_SUFFIXES` (Task 1), `LLM.look(jpeg: bytes, question: str) -> str`.
- Produces: `nova.tools.screen.fit_for_vision(image) -> bytes`; `nova.tools.documents.open_image(path: Path) -> PIL.Image.Image` (RGB, upright); `nova.tools.documents.DEFAULT_QUESTION`; `nova.tools.documents.register(registry, cfg, llm)`; fixture `make_image(name, size=(64, 32), color="red", exif_orientation=None) -> Path`.

- [ ] **Step 1: Add and install pillow-heif**

Append to `requirements.txt`:

```
pillow-heif>=0.18  # iPhone HEIC photos
```

Run: `uv pip install --python .venv/bin/python -r requirements.txt`
Expected: `+ pillow-heif==...`.

- [ ] **Step 2: Add the image fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def make_image(tmp_path):
    def make(name, size=(64, 32), color="red", exif_orientation=None) -> Path:
        from PIL import Image

        path = tmp_path / name
        if path.suffix.lower() in (".heic", ".heif"):
            from pillow_heif import register_heif_opener

            register_heif_opener()
        image = Image.new("RGB", size, color)
        if exif_orientation:
            exif = Image.Exif()
            exif[0x0112] = exif_orientation  # the EXIF Orientation tag
            image.save(path, exif=exif)
        else:
            image.save(path)
        return path

    return make
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_view_image.py`:

```python
import dataclasses

import pytest

from nova.tools import documents
from nova.tools.registry import Registry
from nova.tools.screen import MAX_JPEG_BYTES, fit_for_vision


class FakeLLM:
    def __init__(self):
        self.calls = []

    def look(self, jpeg: bytes, question: str) -> str:
        self.calls.append((jpeg, question))
        return "a red rectangle"


@pytest.fixture
def vision(cfg):
    llm = FakeLLM()
    registry = Registry()
    documents.register(registry, cfg, llm)
    return llm, (lambda **args: registry.invoke("view_image", args))


def test_png_goes_to_the_vision_model(vision, make_image):
    llm, view_image = vision
    path = make_image("shot.png").resolve()
    out = view_image(path=str(path), question="What colour is it?")
    assert out == f"[image 64x32, {path}]\na red rectangle"
    jpeg, question = llm.calls[0]
    assert jpeg[:2] == b"\xff\xd8"
    assert question == "What colour is it?"


def test_default_question(vision, make_image):
    llm, view_image = vision
    view_image(path=str(make_image("a.png")))
    assert llm.calls[0][1] == documents.DEFAULT_QUESTION


@pytest.mark.parametrize(
    "name", ["photo.heic", "PHOTO.HEIC", "anim.gif", "pic.webp", "scan.tiff", "old.bmp", "SNAP.JPG"]
)
def test_formats(vision, make_image, name):
    _, view_image = vision
    assert view_image(path=str(make_image(name))).startswith("[image 64x32,")


def test_phone_photos_are_turned_upright(vision, make_image):
    _, view_image = vision
    out = view_image(path=str(make_image("portrait.jpg", exif_orientation=6)))
    assert out.startswith("[image 32x64,")


def test_relative_path_uses_the_workspace(vision, make_image):
    _, view_image = vision
    make_image("rel.png")
    assert view_image(path="rel.png").startswith("[image 64x32,")


def test_not_a_picture(vision, tmp_path):
    _, view_image = vision
    (tmp_path / "report.pdf").write_bytes(b"%PDF")
    assert view_image(path=str(tmp_path / "report.pdf")) == (
        "ERROR: report.pdf is not a picture view_image can open. Use read_file for documents."
    )


def test_missing_file(vision, tmp_path):
    _, view_image = vision
    missing = (tmp_path / "nope.png").resolve()
    assert view_image(path=str(missing)) == f"ERROR: {missing} does not exist."


def test_corrupt_picture(vision, tmp_path):
    _, view_image = vision
    (tmp_path / "bad.png").write_bytes(b"not an image")
    assert view_image(path=str(tmp_path / "bad.png")).startswith("ERROR: could not open bad.png:")


def test_workspace_restriction_applies(cfg):
    registry = Registry()
    documents.register(registry, dataclasses.replace(cfg, restrict_to_workspace=True), FakeLLM())
    out = registry.invoke("view_image", {"path": "/Library/Desktop Pictures/x.png"})
    assert "outside the allowed workspace" in out


def test_fit_for_vision_shrinks_big_noisy_images():
    from PIL import Image

    noisy = Image.effect_noise((4000, 3000), 100).convert("RGB")
    jpeg = fit_for_vision(noisy)
    assert jpeg[:2] == b"\xff\xd8"
    assert len(jpeg) <= MAX_JPEG_BYTES
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `.venv/bin/python -m pytest tests/test_view_image.py -q`
Expected: collection error, `ImportError: cannot import name 'fit_for_vision'`.

- [ ] **Step 5: Make the vision-sizing helper public**

In `nova/tools/screen.py`, replace `def _shrink_to_limit(image, resample) -> bytes:` and its body with:

```python
def fit_for_vision(image) -> bytes:
    """JPEG bytes small enough for NIM's inline-image limit: scaled down to
    MAX_WIDTH, then recompressed and shrunk until under MAX_JPEG_BYTES."""
    from PIL import Image

    resample = Image.LANCZOS
    if image.mode != "RGB":
        image = image.convert("RGB")
    if image.width > MAX_WIDTH:
        ratio = MAX_WIDTH / image.width
        image = image.resize((MAX_WIDTH, max(1, int(image.height * ratio))), resample)
    data = _encode(image)
    quality = 72
    while len(data) > MAX_JPEG_BYTES and quality > 25:
        quality -= 12
        data = _encode(image, quality)
    while len(data) > MAX_JPEG_BYTES and image.width > 480:
        image = image.resize(
            (int(image.width * 0.75), int(image.height * 0.75)), resample
        )
        data = _encode(image, quality)
    return data
```

In `see_screen`, replace `jpeg = _shrink_to_limit(image, Image.LANCZOS)` with `jpeg = fit_for_vision(image)`.

- [ ] **Step 6: Add `view_image` to `nova/tools/documents.py`**

Change the module docstring's first paragraph to also mention pictures:

```python
"""Reading documents and pictures that are not plain text.

extract_text turns PDF, Word, Excel and PowerPoint files - and on macOS the
older formats textutil understands - into text for read_file, which numbers and
pages it like any other file. view_image hands a picture file to the vision
model. Each format's library is imported only when that format is read, so a
missing one fails just that format, with a message saying what to install.
"""
```

Add these imports (keep the existing ones):

```python
import importlib.util

from ..attachments import DOCUMENT_KINDS, IMAGE_SUFFIXES
from ..config import Config
from ..llm import LLM
from .registry import Registry, schema, string
```

(`from ..attachments import DOCUMENT_KINDS` becomes the combined line above.)

Add below `MAX_SHEET_ROWS`:

```python
DEFAULT_QUESTION = "Describe this image in detail, including any text in it."
```

Append to the end of the file:

```python
def open_image(path: Path):
    """A picture file as an upright RGB image (phones store rotation in EXIF)."""
    from PIL import Image, ImageOps

    if path.suffix.lower() in (".heic", ".heif"):
        try:
            from pillow_heif import register_heif_opener
        except ImportError as exc:
            raise DocumentError("reading HEIC photos needs the pillow-heif package.") from exc
        register_heif_opener()
    with Image.open(path) as image:
        image.seek(0)  # the first frame of an animation
        return ImageOps.exif_transpose(image).convert("RGB")


def register(registry: Registry, cfg: Config, llm: LLM) -> None:
    if importlib.util.find_spec("PIL") is None:
        return
    from .screen import fit_for_vision

    @registry.define(
        "view_image",
        description=(
            "Look at a picture file - a photo, screenshot or scan - and answer a "
            "question about it with a vision model. Use it for attached images and "
            "any png, jpg, heic, webp, gif, tiff or bmp file. Ask something specific "
            "when you can."
        ),
        parameters=schema(
            {
                "path": string("The image file."),
                "question": string("What you want to know about it.", default=DEFAULT_QUESTION),
            },
            ["path"],
        ),
    )
    def view_image(path: str, question: str = DEFAULT_QUESTION) -> str:
        target = cfg.resolve_path(path)
        if not target.is_file():
            return f"ERROR: {target} does not exist."
        if target.suffix.lower() not in IMAGE_SUFFIXES:
            return (
                f"ERROR: {target.name} is not a picture view_image can open. "
                "Use read_file for documents."
            )
        try:
            image = open_image(target)
        except DocumentError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:
            return f"ERROR: could not open {target.name}: {exc}"
        answer = llm.look(fit_for_vision(image), question)
        return f"[image {image.width}x{image.height}, {target}]\n{answer}"
```

- [ ] **Step 7: Register the tool**

In `nova/tools/__init__.py`, change the import line to:

```python
from . import desktop, documents, files, macui, pyexec, screen, shell, uia, web
```

and add after `screen.register(registry, cfg, llm)`:

```python
    documents.register(registry, cfg, llm)
```

- [ ] **Step 8: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass.

Run: `.venv/bin/python -c "from nova.config import Config; from nova.tools import build_registry; print(len(build_registry(Config.from_env(), None).tools))"`
Expected: `31` (30 before, plus `view_image`; the memory tools are not included without a `Memory`).

- [ ] **Step 9: Lint and commit**

Run: `uvx pyflakes nova main.py packaging/nova_app.py tests`
Expected: no output.

```bash
git add nova/tools/screen.py nova/tools/documents.py nova/tools/__init__.py requirements.txt tests/conftest.py tests/test_view_image.py
git commit -m "Add view_image: show picture files, HEIC included, to the vision model

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Window — attach button, chips, sending

**Files:**
- Modify: `nova/interfaces/gui.py`
- Create: `tests/test_gui_attachments.py`

**Interfaces:**
- Consumes: `DEFAULT_PROMPT`, `MAX_ATTACHMENTS`, `add_paths`, `compose_message` from `nova.attachments`.
- Produces (on `NovaWindow`, used by Task 6 and the GUI tests): `attachments: list[Path]`, `chip_row: tk.Frame`, `footer: tk.Frame`, `_add_attachments(candidates: Iterable[str | Path]) -> None`, `_remove_attachment(path: Path) -> None`, `_render_chips() -> None`, `_restore_status() -> None`.

- [ ] **Step 1: Write the failing GUI tests**

Create `tests/test_gui_attachments.py`:

```python
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
    assert labels[:2] == ["📎 f0.txt ✕", "📎 f1.txt ✕"]
    assert labels[-1] == "+2 more" and len(labels) == 7
    assert window.chip_row.winfo_ismapped()


def test_long_names_are_shortened(window, tmp_path):
    window._add_attachments(make_files(tmp_path, "a" * 40 + ".pdf"))
    window.root.update()
    assert chip_labels(window) == ["📎 " + "a" * 27 + "… ✕"]


def test_remove_a_chip(window, tmp_path):
    a, b = make_files(tmp_path, "a.txt", "b.txt")
    window._add_attachments([a, b])
    window.root.update()
    window.chip_row.winfo_children()[0].winfo_children()[1].event_generate("<Button-1>")
    window.root.update()
    assert window.attachments == [b]


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
    assert "📎 report.pdf" in window.transcript.get("1.0", "end")


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
```

- [ ] **Step 2: Run them to see them fail**

Run: `NOVA_GUI_TESTS=1 .venv/bin/python -m pytest tests/test_gui_attachments.py -q`
Expected: FAIL with `AttributeError: 'NovaWindow' object has no attribute '_add_attachments'`. (A Nova window opens and closes for each test.)

- [ ] **Step 3: Implement the button, chips and sending in `nova/interfaces/gui.py`**

Add to the imports:

```python
from pathlib import Path
from typing import Any, Iterable
```

(replacing `from typing import Any`), and:

```python
from ..attachments import DEFAULT_PROMPT, MAX_ATTACHMENTS, add_paths, compose_message
```

Add below `PERMISSION_CHECK_MS = 2000`:

```python
MAX_CHIPS = 6  # attachment chips shown before "+N more"
CHIP_NAME_CHARS = 28
```

In `NovaWindow.__init__`, add before `self._build_window()`:

```python
        self.attachments: list[Path] = []
```

In `_build_window`, after `small = _font(9)` add `self.small_font = small`.

Replace the permissions button's `<Leave>` binding (the three-line `bind("<Leave>", lambda ...)` call) with:

```python
            self.permissions_button.bind("<Leave>", lambda _e: self._restore_status())
```

Replace

```python
        footer = tk.Frame(self.root, bg=PANEL)
        footer.pack(fill="x")
```

with

```python
        # Attachment chips sit above the message box, packed only while there
        # are any (see _render_chips).
        self.chip_row = tk.Frame(self.root, bg=PANEL)
        footer = self.footer = tk.Frame(self.root, bg=PANEL)
        footer.pack(fill="x")
```

Insert before `self.stop_button = tk.Button(`:

```python
        self.attach_button = tk.Button(
            buttons, text="📎", command=self._choose_files, bg=PANEL, fg=FG,
            activebackground=PANEL, relief="flat", borderwidth=0, width=3,
            cursor="hand2", font=bold,
        )
        self.attach_button.pack(side="left", padx=(0, 8), ipady=10)
```

Add a new section before `# ------------------------------------------------------------- transcript`:

```python
    # ------------------------------------------------------------ attachments
    def _choose_files(self) -> None:
        from tkinter import filedialog

        self._add_attachments(filedialog.askopenfilenames(parent=self.root, title="Attach files"))

    def _add_attachments(self, candidates: Iterable[str | Path]) -> None:
        self.attachments, left_out = add_paths(self.attachments, candidates)
        self._render_chips()
        if left_out:
            plural = "s" if left_out != 1 else ""
            self.status.configure(
                text=f"Left out {left_out} file{plural}: at most {MAX_ATTACHMENTS} per message."
            )

    def _remove_attachment(self, path: Path) -> None:
        self.attachments = [p for p in self.attachments if p != path]
        self._render_chips()

    def _render_chips(self) -> None:
        for child in self.chip_row.winfo_children():
            child.destroy()
        if not self.attachments:
            self.chip_row.pack_forget()
            return
        for path in self.attachments[:MAX_CHIPS]:
            name = path.name + ("/" if path.is_dir() else "")
            if len(name) > CHIP_NAME_CHARS:
                name = name[: CHIP_NAME_CHARS - 1] + "…"
            chip = tk.Frame(self.chip_row, bg=FIELD)
            chip.pack(side="left", padx=(0, 6), pady=(8, 0))
            tk.Label(chip, text=f"📎 {name}", bg=FIELD, fg=FG, font=self.small_font).pack(
                side="left", padx=(8, 2), pady=3
            )
            remove = tk.Label(
                chip, text="✕", bg=FIELD, fg=MUTED, font=self.small_font, cursor="hand2"
            )
            remove.pack(side="left", padx=(2, 8))
            remove.bind("<Button-1>", lambda _e, p=path: self._remove_attachment(p))
        hidden = len(self.attachments) - MAX_CHIPS
        if hidden > 0:
            tk.Label(
                self.chip_row, text=f"+{hidden} more", bg=PANEL, fg=MUTED, font=self.small_font
            ).pack(side="left", pady=(8, 0))
        self.chip_row.pack(fill="x", padx=12, before=self.footer)

    def _restore_status(self) -> None:
        self.status.configure(text="Working..." if self.busy else "Ready")
```

Replace `_submit` with:

```python
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
            names = ", ".join(path.name for path in self.attachments)
            self._write(f"📎 {names}\n", "muted")
        self._write("\n", "text")
        self.attachments = []
        self._render_chips()
        self._set_busy(True)

        self.worker = threading.Thread(
            target=self._work, args=(instruction,), daemon=True
        )
        self.worker.start()
```

- [ ] **Step 4: Run the GUI tests to see them pass**

Run: `NOVA_GUI_TESTS=1 .venv/bin/python -m pytest tests/test_gui_attachments.py -q`
Expected: all pass.

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass, with the GUI tests skipped.

- [ ] **Step 5: Look at it**

Run this to capture just Nova's window with three chips, then open the PNG and check: chips above the message box, the 📎 button left of Stop, and nothing clipped.

```bash
.venv/bin/python - <<'EOF'
import os, subprocess, tempfile
from pathlib import Path
os.environ["NVIDIA_API_KEY"] = "nvapi-" + "A1b2C3d4" * 6
import Quartz
from nova.config import Config
from nova.interfaces.gui import NovaWindow
folder = Path(tempfile.mkdtemp())
for name in ("report.pdf", "IMG_2041.heic", "budget-2026-final-version.xlsx"):
    (folder / name).write_bytes(b"x")
win = NovaWindow(Config.from_env())
win._add_attachments(sorted(folder.iterdir()))
def shoot():
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, 0):
        if w.get("kCGWindowOwnerPID") == os.getpid() and w.get("kCGWindowName") == "Nova":
            subprocess.run(["screencapture", "-x", "-o", "-l", str(w["kCGWindowNumber"]), "/tmp/nova-chips.png"])
    win.root.destroy()
win.root.after(1500, shoot)
win.root.mainloop()
print("saved /tmp/nova-chips.png")
EOF
```

- [ ] **Step 6: Lint and commit**

Run: `uvx pyflakes nova main.py packaging/nova_app.py tests`
Expected: no output.

```bash
git add nova/interfaces/gui.py tests/test_gui_attachments.py
git commit -m "Window: attach files with a button; chips and the attached-files message

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Window — paste a screenshot, drag and drop

**Files:**
- Modify: `nova/interfaces/gui.py`
- Modify: `requirements.txt` (append)
- Modify: `tests/test_gui_attachments.py` (append)

**Interfaces:**
- Consumes: `save_pasted_image` (Task 1); `_add_attachments`, `_restore_status` (Task 5).
- Produces: module function `_make_root() -> tuple[tk.Tk, bool]`; on `NovaWindow`: `can_drop: bool`, `_paste(event=None) -> str | None`, `_on_drag_enter(event) -> str`, `_on_drag_leave(event) -> str`, `_on_drop(event) -> str`.

- [ ] **Step 1: Add and install tkinterdnd2**

Append to `requirements.txt`:

```
tkinterdnd2>=0.4  # drag and drop onto the window
```

Run: `uv pip install --python .venv/bin/python -r requirements.txt`
Expected: `+ tkinterdnd2==...`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_gui_attachments.py`:

```python
import subprocess
import types


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


def test_window_accepts_drops(window):
    assert window.can_drop, "tkinterdnd2 should be installed and load"


def test_drag_feedback(window):
    event = types.SimpleNamespace(action="copy")
    window._on_drag_enter(event)
    assert window.status.cget("text") == "Drop to attach"
    window._on_drag_leave(event)
    assert window.status.cget("text") == "Ready"
```

- [ ] **Step 3: Run them to see them fail**

Run: `NOVA_GUI_TESTS=1 .venv/bin/python -m pytest tests/test_gui_attachments.py -q`
Expected: the six new tests FAIL with `AttributeError` (`_paste`, `_on_drop`, `can_drop`...). The clipboard is overwritten.

- [ ] **Step 4: Implement paste and drop in `nova/interfaces/gui.py`**

Change the attachments import to:

```python
from ..attachments import (
    DEFAULT_PROMPT,
    MAX_ATTACHMENTS,
    add_paths,
    compose_message,
    save_pasted_image,
)
```

Add above `def _font`:

```python
def _make_root() -> tuple[tk.Tk, bool]:
    """The window's root, able to take dropped files when tkinterdnd2 loads."""
    try:
        from tkinterdnd2 import TkinterDnD

        return TkinterDnD.Tk(), True
    except Exception:  # not installed, or its Tk extension would not load
        return tk.Tk(), False
```

In `_build_window`, replace `self.root = tk.Tk()` with:

```python
        self.root, self.can_drop = _make_root()
```

After `self.entry.bind("<Escape>", lambda _e: self._stop())` add:

```python
        self.entry.bind(
            "<Command-v>" if sys.platform == "darwin" else "<Control-v>", self._paste
        )
```

Before `self.root.protocol("WM_DELETE_WINDOW", self._close)` add:

```python
        if self.can_drop:
            from tkinterdnd2 import DND_FILES

            for widget in (self.root, self.transcript, self.entry):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<DropEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DropLeave>>", self._on_drag_leave)
                widget.dnd_bind("<<Drop>>", self._on_drop)
```

Add to the attachments section, after `_restore_status`:

```python
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
            self._add_attachments([save_pasted_image(grabbed)])
        return "break"

    def _on_drag_enter(self, event: Any) -> str:
        self.status.configure(text="Drop to attach")
        return event.action

    def _on_drag_leave(self, event: Any) -> str:
        self._restore_status()
        return event.action

    def _on_drop(self, event: Any) -> str:
        self._restore_status()
        # Tk hands over one string; paths with spaces come wrapped in braces.
        self._add_attachments(self.root.tk.splitlist(event.data))
        return event.action
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `NOVA_GUI_TESTS=1 .venv/bin/python -m pytest tests/test_gui_attachments.py -q`
Expected: all pass.

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass, GUI tests skipped.

- [ ] **Step 6: Lint and commit**

Run: `uvx pyflakes nova main.py packaging/nova_app.py tests`
Expected: no output.

```bash
git add nova/interfaces/gui.py requirements.txt tests/test_gui_attachments.py
git commit -m "Window: paste a screenshot or drag files in to attach them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Packaging and CI

**Files:**
- Modify: `packaging/build-macos.sh` (PyInstaller arguments)
- Modify: `.github/workflows/release.yml` (Windows build, the `check` job, `release.needs`)

**Interfaces:**
- Consumes: everything above; no new code interfaces.
- Produces: a macOS app bundle that contains tkinterdnd2's and pillow-heif's native libraries; a CI job named "Lint and test" that must pass before a release publishes.

- [ ] **Step 1: Bundle the native libraries**

In `packaging/build-macos.sh`, after the line `  --hidden-import sqlite3 \` add:

```bash
  --collect-all tkinterdnd2 \
  --collect-all pillow_heif \
```

In `.github/workflows/release.yml`, in the "Build Nova.exe" step, after the line `          --hidden-import sqlite3` add:

```yaml
          --collect-all tkinterdnd2 --collect-all pillow_heif
```

- [ ] **Step 2: Run the tests in CI and gate releases on them**

In `.github/workflows/release.yml`, replace the `check` job with:

```yaml
  check:
    name: Lint and test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: python -m pip install --upgrade pip pyflakes pytest -r requirements.txt
      - run: python -m pyflakes nova main.py packaging/nova_app.py tests
      - run: python -m pytest -q
```

and in the `release` job change `needs: [build-mac, build-windows]` to:

```yaml
    needs: [build-mac, build-windows, check]
```

Run: `uv run --quiet --no-project --with pyyaml python -c "import yaml; wf = yaml.safe_load(open('.github/workflows/release.yml')); print(wf['jobs']['check']['name'], wf['jobs']['release']['needs'])"`
Expected: `Lint and test ['build-mac', 'build-windows', 'check']`.

- [ ] **Step 3: Build the Mac app locally**

Run: `PATH="$PWD/.venv/bin:$PATH" NOVA_VERSION=0.0.0-test bash packaging/build-macos.sh 2>&1 | tail -3`
Expected: `Built: .../dist/Nova-0.0.0-test.dmg`.

- [ ] **Step 4: Check the formats inside the packaged app**

Create sample files with the venv, then read them with the app's own interpreter:

```bash
PROBE=$(mktemp -d)
.venv/bin/python - "$PROBE" <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, "tests")
from conftest import _pdf_bytes
import docx, openpyxl
from pptx import Presentation
from PIL import Image
from pillow_heif import register_heif_opener
folder = Path(sys.argv[1])
(folder / "report.pdf").write_bytes(_pdf_bytes(["Bundle check"]))
d = docx.Document(); d.add_paragraph("Bundle check"); d.save(folder / "report.docx")
wb = openpyxl.Workbook(); wb.active.append(["Bundle", "check"]); wb.save(folder / "book.xlsx")
deck = Presentation(); deck.slides.add_slide(deck.slide_layouts[5]).shapes.title.text = "Bundle check"; deck.save(folder / "deck.pptx")
register_heif_opener(); Image.new("RGB", (40, 20), "blue").save(folder / "photo.heic")
EOF
cat > "$PROBE/probe.py" <<'EOF'
import os
from pathlib import Path
from nova.tools.documents import extract_text, open_image
folder = Path(os.environ["NOVA_PROBE_DIR"])
for name in ("report.pdf", "report.docx", "book.xlsx", "deck.pptx"):
    print(name, "->", extract_text(folder / name).summary)
print("photo.heic ->", open_image(folder / "photo.heic").size)
from tkinterdnd2 import TkinterDnD
root = TkinterDnD.Tk()
print("tkdnd", root.tk.call("package", "provide", "tkdnd"))
root.destroy()
EOF
NOVA_PROBE_DIR="$PROBE" dist/Nova.app/Contents/MacOS/Nova --run-python "$PROBE/probe.py"
```

Expected:

```
report.pdf -> PDF, 1 page
report.docx -> Word document
book.xlsx -> Excel workbook, 1 sheet
deck.pptx -> PowerPoint deck, 1 slide
photo.heic -> (40, 20)
tkdnd 2.10.3
```

(The tkdnd version may differ; any version number means it loaded.)

- [ ] **Step 5: Check the packaged window**

Launch the bundle with a dummy key and an isolated home, and confirm the window opens with the 📎 button. Then remove the build output:

```bash
HOME_DIR=$(mktemp -d)
HOME="$HOME_DIR" NVIDIA_API_KEY="nvapi-$(printf 'A1b2C3d4%.0s' 1 2 3 4 5 6)" dist/Nova.app/Contents/MacOS/Nova & APP=$!
sleep 6
osascript -e "tell application \"System Events\" to return name of every button of window 1 of (first process whose unix id is $APP)"
kill $APP
rm -rf build dist nova-1024.png nova-icon-preview.png
```

Expected: the list of button names includes `📎`.

- [ ] **Step 6: Commit**

```bash
git add packaging/build-macos.sh .github/workflows/release.yml
git commit -m "Bundle drag-and-drop and HEIC support; run the tests in CI before releasing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: End to end, docs, and the hand check

**Files:**
- Modify: `README.md` (the tools list, and a new "Attaching files" section)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing new.

- [ ] **Step 1: Run a real task through the model**

This uses the NVIDIA key and the default models. Expected: one `read_file` call on the PDF, one `view_image` call on the PNG, and an answer that mentions `4,182` (or `4182`) and `BLUE DOG`.

```bash
.venv/bin/python - <<'EOF'
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests")
from conftest import _pdf_bytes
from PIL import Image, ImageDraw, ImageFont
from nova.app import build_session
from nova.approval import AlwaysAllow
from nova.attachments import compose_message
from nova.config import Config

folder = Path(tempfile.mkdtemp())
pdf = folder / "invoice.pdf"
pdf.write_bytes(_pdf_bytes(["Invoice 77", "The total due is 4,182 dollars."]))
png = folder / "sign.png"
image = Image.new("RGB", (700, 200), "white")
ImageDraw.Draw(image).text((40, 50), "BLUE DOG", fill="black", font=ImageFont.load_default(size=96))
image.save(png)

def show(event):
    if event.kind == "tool":
        print(">", event.detail)
    elif event.kind in ("answer", "error"):
        print(f"[{event.kind}] {event.text}")

session = build_session(Config.from_env(), approver=AlwaysAllow(), on_event=show, stream=False)
session.agent.run(compose_message(
    "Summarise the PDF in one sentence and tell me exactly what the picture says.", [pdf, png]))
EOF
```

- [ ] **Step 2: Document attachments in the README**

In `README.md`, in "## The tools", change the Files line to:

```markdown
**Files** — `read_file` (text, plus PDF, Word, Excel and PowerPoint) · `list_dir` · `find_files` · `search_in_files` ·
```

and the Screen line to:

```markdown
**Screen** — `see_screen` · `save_screenshot` · `view_image`
```

Insert this section before `## Stopping it`:

```markdown
## Attaching files

In the window, add files to a message with the 📎 button, by dragging them
onto the window, or by pasting a screenshot with Cmd+V (Ctrl+V on Windows).
They show as chips above the message box; click ✕ to drop one. Nova gets the
list of paths with your message and opens what it needs: `read_file` reads PDF,
Word (`.docx`), Excel (`.xlsx`) and PowerPoint (`.pptx`) as well as plain
text, and on macOS also `.doc`, `.rtf` and `.odt`; `view_image` shows
pictures, iPhone HEIC photos included, to the vision model. Pasted screenshots
are saved in `attachments/` (`~/.nova/attachments` for the installed app),
which keeps the newest 50.
```

- [ ] **Step 3: Full verification**

Run: `.venv/bin/python -m pytest tests/ -q && NOVA_GUI_TESTS=1 .venv/bin/python -m pytest tests/test_gui_attachments.py -q && uvx pyflakes nova main.py packaging/nova_app.py tests`
Expected: everything passes, no lint output.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "README: attaching files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hand check by the owner**

Ask the owner to run `.venv/bin/python main.py gui`, drag a real file from Finder onto the window, confirm a chip appears, and send it. This is the one behaviour a script cannot drive reliably.
