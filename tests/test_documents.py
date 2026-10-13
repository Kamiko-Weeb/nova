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
