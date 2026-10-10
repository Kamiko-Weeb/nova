"""File system tools."""
from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path

from ..config import Config
from .registry import Registry, boolean, integer, schema, string, truncate

TEXT_READ_LIMIT = 200_000


def register(registry: Registry, cfg: Config) -> None:
    def resolve(raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = cfg.workspace / path
        path = path.resolve()
        if cfg.restrict_to_workspace:
            root = cfg.workspace.resolve()
            if root not in path.parents and path != root:
                raise PermissionError(
                    f"{path} is outside the allowed workspace ({root}). "
                    "Set NOVA_RESTRICT_TO_WORKSPACE=false to lift this."
                )
        return path

    # ------------------------------------------------------------------ read
    @registry.define(
        "read_file",
        description=(
            "Read a text file and return its contents. Relative paths resolve against "
            "the workspace directory."
        ),
        parameters=schema(
            {
                "path": string("Path to the file."),
                "start_line": integer("1-indexed first line to return.", default=1),
                "max_lines": integer("How many lines to return.", default=0),
            },
            ["path"],
        ),
    )
    def read_file(path: str, start_line: int = 1, max_lines: int = 0) -> str:
        target = resolve(path)
        if not target.exists():
            return f"ERROR: {target} does not exist."
        if target.is_dir():
            return f"ERROR: {target} is a directory. Use list_dir."
        raw = target.read_bytes()[:TEXT_READ_LIMIT]
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        begin = max(0, start_line - 1)
        chunk = lines[begin:] if max_lines <= 0 else lines[begin : begin + max_lines]
        numbered = "\n".join(
            f"{i:>6}\t{line}" for i, line in enumerate(chunk, start=begin + 1)
        )
        header = f"{target}  ({len(lines)} lines total)"
        return truncate(f"{header}\n{numbered}")

    @registry.define(
        "list_dir",
        description="List the contents of a directory, with sizes.",
        parameters=schema(
            {
                "path": string("Directory to list.", default="."),
                "show_hidden": boolean("Include dotfiles.", default=False),
            },
        ),
    )
    def list_dir(path: str = ".", show_hidden: bool = False) -> str:
        target = resolve(path)
        if not target.is_dir():
            return f"ERROR: {target} is not a directory."
        rows: list[str] = []
        for entry in sorted(
            target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
        ):
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    rows.append(f"[dir ] {entry.name}/")
                else:
                    rows.append(f"[file] {entry.name}  ({entry.stat().st_size:,} bytes)")
            except OSError:
                rows.append(f"[?   ] {entry.name}")
        body = "\n".join(rows) or "(empty)"
        return truncate(f"{target}\n{body}")

    @registry.define(
        "find_files",
        description=(
            "Search a directory tree for files whose name matches a glob pattern "
            "(for example '*.pdf' or 'invoice*')."
        ),
        parameters=schema(
            {
                "root": string("Directory to search from."),
                "pattern": string("Glob pattern matched against the file name."),
                "max_results": integer("Cap on results.", default=100),
            },
            ["root", "pattern"],
        ),
    )
    def find_files(root: str, pattern: str, max_results: int = 100) -> str:
        base = resolve(root)
        if not base.is_dir():
            return f"ERROR: {base} is not a directory."
        hits: list[str] = []
        skip = {"node_modules", ".git", "__pycache__", ".venv", "venv", "AppData"}
        for current, dirnames, filenames in base.walk(on_error=lambda _e: None):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in filenames:
                if fnmatch.fnmatch(name.lower(), pattern.lower()):
                    hits.append(str(current / name))
                    if len(hits) >= max_results:
                        return truncate(
                            "\n".join(hits) + f"\n(stopped at {max_results} results)"
                        )
        return truncate("\n".join(hits) or f"No files matching '{pattern}' under {base}")

    @registry.define(
        "search_in_files",
        description=(
            "Search for a text string inside files under a directory and return "
            "matching lines with their file and line number."
        ),
        parameters=schema(
            {
                "root": string("Directory to search from."),
                "query": string("Text to look for (case-insensitive)."),
                "file_pattern": string("Only search files matching this glob.", default="*"),
                "max_results": integer("Cap on matching lines.", default=80),
            },
            ["root", "query"],
        ),
    )
    def search_in_files(
        root: str, query: str, file_pattern: str = "*", max_results: int = 80
    ) -> str:
        base = resolve(root)
        needle = query.lower()
        hits: list[str] = []
        skip = {"node_modules", ".git", "__pycache__", ".venv", "venv", "AppData"}
        for current, dirnames, filenames in base.walk(on_error=lambda _e: None):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in filenames:
                if not fnmatch.fnmatch(name.lower(), file_pattern.lower()):
                    continue
                file_path = current / name
                try:
                    if file_path.stat().st_size > 5_000_000:
                        continue
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for num, line in enumerate(content.splitlines(), start=1):
                    if needle in line.lower():
                        hits.append(f"{file_path}:{num}: {line.strip()[:200]}")
                        if len(hits) >= max_results:
                            return truncate("\n".join(hits) + "\n(result cap reached)")
        return truncate("\n".join(hits) or f"No matches for '{query}' under {base}")

    # ----------------------------------------------------------------- write
    @registry.define(
        "write_file",
        description=(
            "Create or overwrite a text file. Parent directories are created as needed. "
            "Set append=true to add to the end of an existing file instead."
        ),
        parameters=schema(
            {
                "path": string("File to write."),
                "content": string("Text to write."),
                "append": boolean("Append instead of overwriting.", default=False),
            },
            ["path", "content"],
        ),
        dangerous=True,
        preview=lambda a: (
            f"{'Append to' if a.get('append') else 'Write'} {a.get('path')} "
            f"({len(a.get('content', ''))} characters)"
        ),
    )
    def write_file(path: str, content: str, append: bool = False) -> str:
        target = resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        with target.open("a" if append else "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        verb = "Appended to" if append else ("Overwrote" if existed else "Created")
        return f"{verb} {target} ({len(content)} characters written)."

    @registry.define(
        "move_path",
        description="Move or rename a file or directory.",
        parameters=schema(
            {"source": string("Existing path."), "destination": string("New path.")},
            ["source", "destination"],
        ),
        dangerous=True,
        preview=lambda a: f"Move {a.get('source')} -> {a.get('destination')}",
    )
    def move_path(source: str, destination: str) -> str:
        src, dst = resolve(source), resolve(destination)
        if not src.exists():
            return f"ERROR: {src} does not exist."
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved {src} to {dst}."

    @registry.define(
        "copy_path",
        description="Copy a file or directory.",
        parameters=schema(
            {"source": string("Existing path."), "destination": string("Target path.")},
            ["source", "destination"],
        ),
        dangerous=True,
        preview=lambda a: f"Copy {a.get('source')} -> {a.get('destination')}",
    )
    def copy_path(source: str, destination: str) -> str:
        src, dst = resolve(source), resolve(destination)
        if not src.exists():
            return f"ERROR: {src} does not exist."
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return f"Copied {src} to {dst}."

    @registry.define(
        "delete_path",
        description=(
            "Delete a file or directory. Prefer recycle=true so the item goes to the "
            "Recycle Bin and can be restored; recycle=false is permanent."
        ),
        parameters=schema(
            {
                "path": string("Path to delete."),
                "recycle": boolean("Send to Recycle Bin instead of erasing.", default=True),
            },
            ["path"],
        ),
        dangerous=True,
        preview=lambda a: (
            f"DELETE {a.get('path')} "
            f"({'to Recycle Bin' if a.get('recycle', True) else 'PERMANENTLY'})"
        ),
    )
    def delete_path(path: str, recycle: bool = True) -> str:
        target = resolve(path)
        if not target.exists():
            return f"ERROR: {target} does not exist."
        if recycle:
            try:
                from send2trash import send2trash

                send2trash(str(target))
                return f"Sent {target} to the Recycle Bin."
            except ImportError:
                return (
                    "ERROR: recycle=true needs the 'send2trash' package "
                    "(pip install send2trash). Refusing to delete permanently without "
                    "being asked to: call again with recycle=false only if the user "
                    "really wants it erased."
                )
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return f"Permanently deleted {target}."
