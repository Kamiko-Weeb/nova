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
SUMMARY_NAMES = 6  # names listed under a sent message before "and N more"
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


def describe(path: Path) -> str:
    """The full path with its kind and size: '/x/report.pdf (PDF document, 2.1 MB)'."""
    kind = kind_of(path)
    if kind == "folder":
        return f"{path}/ (folder)"
    try:
        size = format_size(path.stat().st_size)
    except OSError:  # deleted or moved since it was attached
        return f"{path} ({kind})"
    return f"{path} ({kind}, {size})"


def badge(path: Path) -> str:
    """A short type tag for a chip: the extension in capitals, or FOLDER / FILE."""
    if path.is_dir():
        return "FOLDER"
    extension = path.suffix[1:].upper()
    return extension if 0 < len(extension) <= 4 else "FILE"


def summary_line(paths: Sequence[Path]) -> str:
    """The muted line under a sent message: 'Attached: a.pdf, b.png'."""
    names = [path.name + ("/" if path.is_dir() else "") for path in paths]
    hidden = len(names) - SUMMARY_NAMES
    more = f" and {hidden} more" if hidden > 0 else ""
    return f"Attached: {', '.join(names[:SUMMARY_NAMES])}{more}"


def _listing(path: Path) -> str:
    return f"- {describe(path)}"


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
