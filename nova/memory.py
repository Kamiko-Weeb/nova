"""Notes that survive a restart.

A plain Markdown file the agent reads into its system prompt at the start of
every session and can add to while working. Deliberately simple and
human-editable: open it in Notepad and change anything.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

HEADER = """\
# Nova's notes

Things worth remembering between sessions. Nova appends here when told to
remember something; you can edit or delete any line by hand.
"""

MAX_NOTES = 200
MAX_CHARS = 8_000

# Notes are stored as "- the note  _(noted 2026-10-09)_"; the suffix is for the
# human reading the file, not part of the note itself.
_STAMP = re.compile(r"\s*_\(noted \d{4}-\d{2}-\d{2}\)_\s*$")


class Memory:
    def __init__(self, path: Path) -> None:
        self.path = path

    # ------------------------------------------------------------------ read
    def notes(self) -> list[str]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        found = []
        for line in text.splitlines():
            if not line.startswith("- "):
                continue
            note = _STAMP.sub("", line[2:].strip()).strip()
            if note:
                found.append(note)
        return found

    def as_prompt(self) -> str:
        """The block injected into the system prompt, or empty if there is nothing."""
        notes = self.notes()
        if not notes:
            return ""
        body = "\n".join(f"- {note}" for note in notes)
        if len(body) > MAX_CHARS:
            body = body[:MAX_CHARS] + "\n- ... (older notes omitted)"
        return (
            "\nThings the owner has told you to remember from previous sessions. "
            "Treat these as standing context, and follow any preferences they "
            "express:\n" + body + "\n"
        )

    # ----------------------------------------------------------------- write
    def add(self, note: str) -> str:
        note = " ".join(note.split()).strip(" -")
        if not note:
            return "ERROR: nothing to remember."
        existing = self.notes()
        if any(note.lower() == old.lower() for old in existing):
            return "Already remembered that one."

        stamp = time.strftime("%Y-%m-%d")
        try:
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(HEADER + "\n", encoding="utf-8")
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"- {note}  _(noted {stamp})_\n")
        except OSError as exc:
            return f"ERROR: could not write the notes file: {exc}"

        if len(existing) + 1 > MAX_NOTES:
            self._prune()
        return f"Noted: {note}"

    def forget(self, needle: str) -> str:
        needle = needle.strip().lower()
        if not needle:
            return "ERROR: say which note to forget."
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            return "There are no notes yet."

        kept, dropped = [], []
        for line in lines:
            if line.startswith("- ") and needle in line.lower():
                dropped.append(_STAMP.sub("", line[2:].strip()).strip())
            else:
                kept.append(line)
        if not dropped:
            return f"No note matching '{needle}'."
        try:
            self.path.write_text("".join(kept), encoding="utf-8")
        except OSError as exc:
            return f"ERROR: could not update the notes file: {exc}"
        return f"Forgot {len(dropped)} note(s): " + "; ".join(dropped[:5])

    def _prune(self) -> None:
        """Keep the newest MAX_NOTES entries so the file cannot grow forever."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            return
        notes = [line for line in lines if line.startswith("- ")]
        if len(notes) <= MAX_NOTES:
            return
        drop = set(notes[: len(notes) - MAX_NOTES])
        kept = [line for line in lines if line not in drop]
        try:
            self.path.write_text("".join(kept), encoding="utf-8")
        except OSError:
            pass


def register(registry, memory: Memory) -> None:
    """Expose remember/forget as tools."""
    from .tools.registry import schema, string

    @registry.define(
        "remember",
        description=(
            "Save a short note that will still be available in future sessions. Use "
            "this when the owner tells you to remember something, states a lasting "
            "preference, or tells you a fact about their setup that will matter "
            "again later. Keep each note to one sentence."
        ),
        parameters=schema({"note": string("The thing to remember.")}, ["note"]),
    )
    def remember(note: str) -> str:
        return memory.add(note)

    @registry.define(
        "forget",
        description="Delete remembered notes containing the given text.",
        parameters=schema(
            {"about": string("Text identifying the note(s) to delete.")}, ["about"]
        ),
    )
    def forget(about: str) -> str:
        return memory.forget(about)

    @registry.define(
        "list_memory",
        description="List everything currently remembered from previous sessions.",
        parameters=schema(),
    )
    def list_memory() -> str:
        notes = memory.notes()
        if not notes:
            return "Nothing remembered yet."
        return "\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1))
