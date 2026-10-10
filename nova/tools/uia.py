"""Windows UI Automation, via pywinauto.

This is the precise alternative to clicking at coordinates guessed from a
screenshot. Windows exposes a tree of real controls with real names, so for
native apps and dialogs the agent can ask for "the button called Save" instead
of estimating where it is. Vision stays as the fallback for games, canvases and
web pages, where there is no control tree worth reading.
"""
from __future__ import annotations

import sys

from ..config import Config
from .registry import Registry, boolean, integer, schema, string, truncate

MAX_CONTROLS = 120
DEFAULT_DEPTH = 6
# Control types that are worth showing: anything a person could interact with
# or read a label from.
USEFUL_TYPES = {
    "Button", "CheckBox", "ComboBox", "Edit", "Document", "Hyperlink",
    "ListItem", "MenuItem", "RadioButton", "Slider", "Tab", "TabItem",
    "Text", "TreeItem", "Spinner", "SplitButton",
}


def register(registry: Registry, cfg: Config) -> None:
    if sys.platform != "win32" or not cfg.allow_desktop_control:
        return
    # Imported once here: doubles as the availability check, and the name is
    # closed over by the tools below.
    try:
        from pywinauto import Desktop
    except Exception:
        return  # pywinauto not installed, or COM unavailable

    def _windows():
        try:
            return [w for w in Desktop(backend="uia").windows() if w.is_visible()]
        except Exception:
            return []

    def _find_window(title: str):
        """Match a top-level window by case-insensitive substring of its title."""
        needle = title.strip().lower()
        candidates = []
        for win in _windows():
            try:
                text = win.window_text() or ""
            except Exception:
                continue
            if not text:
                continue
            if needle in text.lower():
                candidates.append((len(text), win, text))
        if not candidates:
            return None, []
        candidates.sort(key=lambda row: row[0])  # prefer the tightest match
        return candidates[0][1], [row[2] for row in candidates]

    def _describe(control, index: int) -> str | None:
        try:
            info = control.element_info
            kind = info.control_type or "?"
            name = (info.name or "").strip()
            rect = info.rectangle
        except Exception:
            return None
        if kind not in USEFUL_TYPES:
            return None
        if not name and kind not in {"Edit", "Document"}:
            return None
        width, height = rect.width(), rect.height()
        if width <= 0 or height <= 0:
            return None
        centre = f"({rect.left + width // 2}, {rect.top + height // 2})"
        label = name[:70] if name else "(no label)"
        extra = ""
        try:
            if kind in {"Edit", "Document"}:
                value = control.get_value()
                if value:
                    extra = f"  value={value[:60]!r}"
        except Exception:
            pass
        try:
            if not control.is_enabled():
                extra += "  [disabled]"
        except Exception:
            pass
        return f"{index:>3}. {kind:<12} {label:<70} at {centre}{extra}"

    # ------------------------------------------------------------------ read
    @registry.define(
        "list_windows",
        description=(
            "List the open application windows with their titles. Use this first "
            "when you need to work with a program's interface, so you know the exact "
            "window title to pass to the other UI tools."
        ),
        parameters=schema(),
    )
    def list_windows() -> str:
        rows = []
        for win in _windows():
            try:
                title = (win.window_text() or "").strip()
                if not title:
                    continue
                rect = win.element_info.rectangle
                rows.append(
                    f"{title[:90]}   [{rect.width()}x{rect.height()} at "
                    f"({rect.left}, {rect.top})]"
                )
            except Exception:
                continue
        if not rows:
            return "No visible windows found."
        return truncate("\n".join(sorted(set(rows))))

    @registry.define(
        "inspect_window",
        description=(
            "List the interactive controls inside a window: buttons, text boxes, "
            "checkboxes, menu items and so on, with their exact names and screen "
            "coordinates. This is far more reliable than looking at a screenshot. "
            "Use it before clicking anything in a normal Windows application, then "
            "act with click_control or set_control_text."
        ),
        parameters=schema(
            {
                "title": string("Part of the window title, e.g. 'Notepad'."),
                "depth": integer(
                    "How deep to search the control tree. Raise it if you cannot "
                    "find what you need, but it gets slower.",
                    default=DEFAULT_DEPTH,
                ),
            },
            ["title"],
        ),
    )
    def inspect_window(title: str, depth: int = DEFAULT_DEPTH) -> str:
        window, matches = _find_window(title)
        if window is None:
            return (
                f"No window matching '{title}'. Call list_windows to see what is open."
            )
        try:
            found = window.descendants(depth=max(1, min(12, depth)))
        except Exception as exc:
            return f"ERROR: could not read the control tree: {exc}"

        rows: list[str] = []
        for control in found:
            line = _describe(control, len(rows) + 1)
            if line:
                rows.append(line)
            if len(rows) >= MAX_CONTROLS:
                rows.append(f"... stopped at {MAX_CONTROLS} controls")
                break

        header = f"Controls in '{window.window_text()}'"
        if len(matches) > 1:
            header += f"  (also matched: {', '.join(matches[1:4])})"
        if not rows:
            return (
                f"{header}\nNo labelled controls found at depth {depth}. Try a larger "
                "depth, or fall back to see_screen and click."
            )
        return truncate(f"{header}\n" + "\n".join(rows))

    @registry.define(
        "read_window_text",
        description=(
            "Read the visible text inside a window. Use this to check an error "
            "message, read a document, or confirm what a dialog says, without "
            "spending a vision call."
        ),
        parameters=schema(
            {"title": string("Part of the window title.")},
            ["title"],
        ),
    )
    def read_window_text(title: str) -> str:
        window, _ = _find_window(title)
        if window is None:
            return f"No window matching '{title}'."
        chunks: list[str] = []
        try:
            for control in window.descendants(depth=8):
                try:
                    info = control.element_info
                    if info.control_type not in {"Text", "Edit", "Document", "ListItem"}:
                        continue
                    text = (info.name or "").strip()
                    if not text:
                        try:
                            text = (control.get_value() or "").strip()
                        except Exception:
                            text = ""
                    if text and text not in chunks:
                        chunks.append(text)
                except Exception:
                    continue
        except Exception as exc:
            return f"ERROR: could not read the window: {exc}"
        if not chunks:
            return f"No readable text found in '{window.window_text()}'."
        return truncate(f"Text in '{window.window_text()}':\n" + "\n".join(chunks))

    # ----------------------------------------------------------------- write
    def _find_control(window, name: str, kind: str = ""):
        needle = name.strip().lower()
        wanted = kind.strip().lower()
        best = None
        best_score = -1
        try:
            candidates = window.descendants(depth=8)
        except Exception:
            return None
        for control in candidates:
            try:
                info = control.element_info
                label = (info.name or "").strip()
                if not label:
                    continue
                if wanted and (info.control_type or "").lower() != wanted:
                    continue
                low = label.lower()
                if low == needle:
                    score = 3
                elif low.startswith(needle) or needle in low:
                    score = 2
                else:
                    continue
                if not control.is_enabled():
                    score -= 1
                if score > best_score:
                    best, best_score = control, score
            except Exception:
                continue
        return best

    @registry.define(
        "focus_window",
        description="Bring a window to the front and give it keyboard focus.",
        parameters=schema({"title": string("Part of the window title.")}, ["title"]),
        dangerous=True,
        preview=lambda a: f"Focus the window matching '{a.get('title')}'",
    )
    def focus_window(title: str) -> str:
        window, _ = _find_window(title)
        if window is None:
            return f"No window matching '{title}'."
        try:
            window.set_focus()
        except Exception as exc:
            return f"ERROR: could not focus the window: {exc}"
        return f"Focused '{window.window_text()}'."

    @registry.define(
        "click_control",
        description=(
            "Click a named control inside a window - a button, menu item, checkbox "
            "or list entry. Get the exact name from inspect_window first. This is "
            "precise, unlike clicking at coordinates."
        ),
        parameters=schema(
            {
                "title": string("Part of the window title."),
                "control": string("The control's name, as shown by inspect_window."),
                "control_type": string(
                    "Optional filter, e.g. 'Button' or 'MenuItem', when several "
                    "controls share a name.",
                    default="",
                ),
                "double": boolean("Double-click instead of single.", default=False),
            },
            ["title", "control"],
        ),
        dangerous=True,
        preview=lambda a: (
            f"Click '{a.get('control')}' in the window matching '{a.get('title')}'"
        ),
    )
    def click_control(
        title: str, control: str, control_type: str = "", double: bool = False
    ) -> str:
        window, _ = _find_window(title)
        if window is None:
            return f"No window matching '{title}'."
        target = _find_control(window, control, control_type)
        if target is None:
            return (
                f"No control named '{control}' in '{window.window_text()}'. "
                "Call inspect_window to see the exact names."
            )
        label = (target.element_info.name or control).strip()
        try:
            window.set_focus()
        except Exception:
            pass
        try:
            target.click_input(double=double)
        except Exception:
            # Some controls refuse a synthetic mouse click but accept Invoke.
            try:
                target.invoke()
            except Exception as exc:
                return f"ERROR: could not click '{label}': {exc}"
        return f"Clicked '{label}' in '{window.window_text()}'."

    @registry.define(
        "set_control_text",
        description=(
            "Put text into a named text box, replacing whatever is there. More "
            "reliable than clicking the field and typing."
        ),
        parameters=schema(
            {
                "title": string("Part of the window title."),
                "control": string("The text box's name, from inspect_window."),
                "text": string("The text to put in it."),
            },
            ["title", "control", "text"],
        ),
        dangerous=True,
        preview=lambda a: (
            f"Set '{a.get('control')}' in '{a.get('title')}' to: "
            f"{a.get('text', '')[:120]}"
        ),
    )
    def set_control_text(title: str, control: str, text: str) -> str:
        window, _ = _find_window(title)
        if window is None:
            return f"No window matching '{title}'."
        target = _find_control(window, control)
        if target is None:
            return (
                f"No control named '{control}' in '{window.window_text()}'. "
                "Call inspect_window to see the exact names."
            )
        try:
            window.set_focus()
            target.set_edit_text(text)
        except Exception:
            try:
                target.click_input()
                target.type_keys("^a{BACKSPACE}", pause=0.02)
                target.type_keys(text, with_spaces=True, pause=0.01)
            except Exception as exc:
                return f"ERROR: could not set the text: {exc}"
        return f"Set '{control}' to {text[:80]!r}."
