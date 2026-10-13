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
