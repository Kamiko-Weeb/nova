"""macOS UI control, via AppleScript and the Accessibility API.

The macOS counterpart to uia.py. Tool names match the Windows ones deliberately,
so the agent's guidance and the model's habits carry across platforms: on either
OS it calls list_windows, inspect_window, click_control and so on, and the right
implementation registers for the machine it is running on.

Everything here goes through `osascript`, driving System Events. That needs
Accessibility permission, which macOS only grants after the user ticks the box in
System Settings - see `accessibility_status` below, which explains how.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from ..config import Config
from .registry import Registry, boolean, integer, schema, string, truncate

SCRIPT_TIMEOUT = 30
MAX_CONTROLS = 120
MAX_DEPTH = 15  # levels below the window that are read
MAX_ELEMENTS = 5000  # stop descending once this many elements have been read

# Accessibility roles worth reporting: things a person could click or read.
USEFUL_ROLES = {
    "AXButton", "AXCheckBox", "AXComboBox", "AXTextField", "AXTextArea",
    "AXMenuItem", "AXMenuButton", "AXRadioButton", "AXSlider", "AXStaticText",
    "AXLink", "AXPopUpButton", "AXTabGroup", "AXRow", "AXCell", "AXImage",
}
EDITABLE_ROLES = {"AXTextField", "AXTextArea", "AXComboBox"}
TEXT_ROLES = {"AXStaticText", "AXTextField", "AXTextArea"}

# A window's UI tree is read one level at a time, one query per attribute:
# `role of every UI element of every UI element of window 1` is a single Apple
# event however wide that level is. Asking each element for each attribute in
# turn costs six events per control and timed out on a 300-row table, and
# `entire contents`, the obvious shortcut, returns nothing on macOS 27.
#
# Values come back as text, joined with control characters that never appear in
# UI text: _ITEM between elements, _COLUMN between attributes.
_ITEM = "\x1e"
_COLUMN = "\x1f"
_LEVEL_HELPERS = """
on flat(theList, levels)
    if levels is 1 then return theList
    set out to {}
    repeat with x in theList
        set out to out & my flat(contents of x, levels - 1)
    end repeat
    return out
end flat

on joined(theList, separator)
    set AppleScript's text item delimiters to separator
    set theText to theList as text
    set AppleScript's text item delimiters to ""
    return theText
end joined
"""
# Element attribute -> AppleScript property.
_PROPERTIES = {
    "name": "name",
    "description": "description",
    "role_description": "role description",
    "position": "position",
    "size": "size",
    "value": "value",
}
_PAIRS = {"position", "size"}  # come back as {x, y}, so two items per element


@dataclass
class Element:
    """One node of a window's UI tree, found at `index` within its level."""

    depth: int
    index: int
    role: str = ""
    name: str = ""
    description: str = ""
    role_description: str = ""
    position: tuple[int, int] | None = None
    size: tuple[int, int] | None = None
    value: str = ""

    @property
    def label(self) -> str:
        """The name, else a description that says more than the role does.

        Icon-only controls - most toolbar buttons, many text fields - carry their
        label in the description. Otherwise the description is just the role
        spelled out ("button"), which is noise.
        """
        if self.name:
            return self.name
        if self.description != self.role_description:
            return self.description
        return ""


class WindowError(Exception):
    """A window could not be read; the message is fit to hand to the model."""


def _escape(value: str) -> str:
    """Quote a Python string for embedding in an AppleScript literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def run_applescript(script: str, timeout: int = SCRIPT_TIMEOUT) -> tuple[bool, str]:
    """Run a script, returning (ok, output-or-error)."""
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False, "osascript not found - this tool only works on macOS."
    except subprocess.TimeoutExpired:
        return False, (
            f"AppleScript timed out after {timeout}s. The application may be busy, "
            "or the window has far too many elements to enumerate."
        )

    if proc.returncode != 0:
        error = proc.stderr.strip() or "unknown AppleScript error"
        if "-1743" in error or "not allowed assistive" in error.lower():
            return False, (
                "macOS denied Accessibility access. Call accessibility_status for "
                "the exact steps to enable it."
            )
        if "-1728" in error:
            return False, "That window or control was not found."
        return False, error
    return True, proc.stdout.strip()


_accessibility_confirmed = False


def accessibility_denied() -> str | None:
    """Return why Accessibility is off, or None once it is confirmed on.

    The probe reads another app's menu bar, which only the Accessibility API can
    answer. Asking System Events for a process name succeeds without the
    permission, so it proves nothing - and reading a window without it reports
    "no windows" instead of "permission missing".
    """
    global _accessibility_confirmed
    if _accessibility_confirmed:
        return None
    ok, out = run_applescript(
        'tell application "System Events" to tell process "Finder" '
        "to return name of menu bar item 1 of menu bar 1"
    )
    if ok:
        _accessibility_confirmed = True
        return None
    return out


# ---------------------------------------------------------------- tree reading
def _level(depth: int) -> str:
    """AppleScript for every element `depth` levels below the front window."""
    return "every UI element of " * depth + "window 1"


def _clean(raw: str) -> str:
    # A missing attribute coerces to the text "missing value", not "".
    text = raw.strip()
    return "" if text == "missing value" else text


def _read_level(app: str, depth: int, fields: tuple[str, ...]) -> list[Element]:
    """Every element at one depth, with the requested attributes filled in."""
    level = _level(depth)
    queries = "".join(
        f"""
    try
        set end of theColumns to my joined(my flat({_PROPERTIES[field]} of {level}, {depth}), character id 30)
    on error
        set end of theColumns to ""
    end try"""
        for field in fields
    )
    script = _LEVEL_HELPERS + f"""
tell application "System Events" to tell process "{_escape(app)}"
    if not (exists window 1) then return "NOWINDOW"
    set theRoles to my flat(role of {level}, {depth})
    if (count of theRoles) is 0 then return ""
    set theColumns to {{my joined(theRoles, character id 30)}}{queries}
    return my joined(theColumns, character id 31)
end tell
"""
    ok, out = run_applescript(script)
    if not ok:
        raise WindowError(f"ERROR: {out}")
    if out == "NOWINDOW":
        raise WindowError(f"'{app}' has no inspectable front window.")
    if not out:
        return []

    columns = out.split(_COLUMN)
    elements = [
        Element(depth, index, role=_clean(role))
        for index, role in enumerate(columns[0].split(_ITEM), start=1)
    ]
    for field, column in zip(fields, columns[1:]):
        values = column.split(_ITEM)
        if field in _PAIRS:
            if len(values) != 2 * len(elements):
                continue  # the query failed, or an element had no such pair
            for element, x, y in zip(elements, values[::2], values[1::2]):
                try:
                    setattr(element, field, (int(float(x)), int(float(y))))
                except ValueError:
                    pass
        elif len(values) == len(elements):
            for element, value in zip(elements, values):
                setattr(element, field, _clean(value))
    return elements


def _snapshot(app: str, *fields: str) -> list[Element]:
    """Read the front window's UI tree breadth-first, shallowest elements first."""
    denied = accessibility_denied()
    if denied:
        raise WindowError(f"ERROR: {denied}")
    elements: list[Element] = []
    for depth in range(1, MAX_DEPTH + 1):
        try:
            level = _read_level(app, depth, fields)
        except WindowError:
            if depth == 1:
                raise
            break  # a level the app will not describe ends the walk
        if not level:
            break
        elements.extend(level)
        if len(elements) >= MAX_ELEMENTS:
            break
    return elements


def _best_match(
    elements: list[Element], wanted: str, role: str = "", prefer: set[str] = frozenset()
) -> Element | None:
    """An exact label match beats a partial one; then `prefer` roles; then the
    shallowest. Case-insensitive, as AppleScript's own comparisons are."""
    needle = wanted.strip().lower()
    role = role.strip().lower()
    best, best_key = None, None
    for element in elements:
        if role and element.role.lower() != role:
            continue
        label = element.label.lower()
        if label == needle:
            score = 2
        elif needle and needle in label:
            score = 1
        else:
            continue
        key = (score, element.role in prefer)
        if best_key is None or key > best_key:  # strict: the shallowest wins ties
            best, best_key = element, key
    return best


def _act(app: str, element: Element, action: str) -> tuple[bool, str]:
    """Run `action` on theElement, after checking it is still the one we found.

    The window can change between reading it and acting on it; clicking whatever
    has moved into the same slot would be worse than failing.
    """
    script = _LEVEL_HELPERS + f"""
tell application "System Events" to tell process "{_escape(app)}"
    set frontmost to true
    set theElement to item {element.index} of my flat(get {_level(element.depth)}, {element.depth})
    set theName to name of theElement
    if theName is missing value then set theName to ""
    set theDescription to description of theElement
    if theDescription is missing value then set theDescription to ""
    if role of theElement is not "{_escape(element.role)}" or theName is not "{_escape(element.name)}" ¬
        or theDescription is not "{_escape(element.description)}" then return "CHANGED"
    {action}
    return "DONE"
end tell
"""
    ok, out = run_applescript(script)
    if ok and out == "CHANGED":
        return False, "the window changed before the action ran. Inspect it again."
    return ok, out


def register(registry: Registry, cfg: Config) -> None:
    if sys.platform != "darwin" or not cfg.allow_desktop_control:
        return

    # ------------------------------------------------------------ permission
    @registry.define(
        "accessibility_status",
        description=(
            "Check whether macOS has granted the Accessibility permission that the "
            "window-control tools need. Call this if those tools start failing, and "
            "relay the instructions to the user - only they can tick the box."
        ),
        parameters=schema(),
    )
    def accessibility_status() -> str:
        out = accessibility_denied()
        if out is None:
            return (
                "Accessibility access is granted; the window-control tools should "
                "work."
            )
        return (
            "Accessibility access is NOT granted, so window control will fail.\n"
            "Ask the user to open System Settings > Privacy & Security > "
            "Accessibility, then enable the app Nova is running from (Terminal, "
            "iTerm, or Nova itself). They may need to restart it afterwards.\n"
            f"Underlying error: {out}"
        )

    # ------------------------------------------------------------------ read
    @registry.define(
        "list_windows",
        description=(
            "List the open application windows with their titles. Use this first "
            "when you need to work with a program's interface, so you know the exact "
            "application and window name to pass to the other UI tools."
        ),
        parameters=schema(),
    )
    def list_windows() -> str:
        denied = accessibility_denied()
        if denied:
            return f"ERROR: {denied}"
        script = """
        tell application "System Events"
            set output to ""
            repeat with proc in (every process whose visible is true)
                set procName to name of proc
                try
                    repeat with win in (every window of proc)
                        set winPos to position of win
                        set winSize to size of win
                        set output to output & procName & " :: " & (name of win) ¬
                            & " [" & (item 1 of winSize) & "x" & (item 2 of winSize) ¬
                            & " at (" & (item 1 of winPos) & ", " ¬
                            & (item 2 of winPos) & ")]" & linefeed
                    end repeat
                on error
                    set output to output & procName & " :: (no inspectable windows)" & linefeed
                end try
            end repeat
            return output
        end tell
        """
        ok, out = run_applescript(script)
        if not ok:
            return f"ERROR: {out}"
        return truncate(out or "No visible windows found.")

    @registry.define(
        "inspect_window",
        description=(
            "List the interactive controls inside an application's front window: "
            "buttons, text fields, checkboxes, menu items, with their names and "
            "screen coordinates. Far more reliable than reading a screenshot. Use it "
            "before clicking anything in a normal Mac application, then act with "
            "click_control or set_control_text."
        ),
        parameters=schema(
            {
                "title": string(
                    "The application name, e.g. 'Safari' or 'Notes'. Take it from "
                    "the part before ' :: ' in list_windows output."
                ),
                "max_items": integer(
                    "Cap on controls returned.", default=MAX_CONTROLS
                ),
            },
            ["title"],
        ),
    )
    def inspect_window(title: str, max_items: int = MAX_CONTROLS) -> str:
        app = title.strip()
        try:
            elements = _snapshot(
                app, "name", "description", "role_description", "position", "size"
            )
        except WindowError as exc:
            return str(exc)

        limit = max(1, min(MAX_CONTROLS, max_items))
        rows: list[str] = []
        for element in elements:
            if element.role not in USEFUL_ROLES or not element.position:
                continue
            if not element.label and element.role not in EDITABLE_ROLES:
                continue
            (left, top), (width, height) = element.position, element.size or (0, 0)
            if width <= 0 or height <= 0:
                continue
            if len(rows) == limit:
                rows.append(f"... stopped at {limit} controls")
                break
            centre = f"({left + width // 2}, {top + height // 2})"
            label = (element.label or "(no label)")[:70]
            rows.append(f"{len(rows) + 1:>3}. {element.role:<14} {label:<70} at {centre}")

        if not rows:
            return (
                f"No labelled controls found in '{title}'. The app may draw its own "
                "interface rather than using standard controls - fall back to "
                "see_screen and click."
            )
        return truncate(f"Controls in '{title}'\n" + "\n".join(rows))

    @registry.define(
        "read_window_text",
        description=(
            "Read the visible text in an application's front window. Use it to check "
            "an error message or confirm what a dialog says, without spending a "
            "vision call."
        ),
        parameters=schema({"title": string("The application name.")}, ["title"]),
    )
    def read_window_text(title: str) -> str:
        try:
            elements = _snapshot(title.strip(), "value", "name")
        except WindowError as exc:
            return str(exc)

        seen: dict[str, None] = {}  # ordered, de-duplicated
        for element in elements:
            if element.role in TEXT_ROLES:
                text = element.value or element.name
                if text:
                    seen[text] = None
        if not seen:
            return f"No readable text found in '{title}'."
        return truncate(f"Text in '{title}':\n" + "\n".join(seen))

    # ----------------------------------------------------------------- write
    @registry.define(
        "focus_window",
        description="Bring an application to the front and give it keyboard focus.",
        parameters=schema({"title": string("The application name.")}, ["title"]),
        dangerous=True,
        preview=lambda a: f"Bring '{a.get('title')}' to the front",
    )
    def focus_window(title: str) -> str:
        app = _escape(title.strip())
        ok, out = run_applescript(f'tell application "{app}" to activate')
        if not ok:
            # Not every process is scriptable by name; fall back to System Events.
            ok, out = run_applescript(
                f'tell application "System Events" to set frontmost of '
                f'process "{app}" to true'
            )
        if not ok:
            return f"ERROR: could not focus '{title}': {out}"
        return f"Brought '{title}' to the front."

    @registry.define(
        "click_control",
        description=(
            "Click a named control inside an application's front window - a button, "
            "menu item, checkbox or link. Get the exact name from inspect_window "
            "first. Precise, unlike clicking at coordinates."
        ),
        parameters=schema(
            {
                "title": string("The application name."),
                "control": string("The control's name, as shown by inspect_window."),
                "control_type": string(
                    "Optional accessibility role filter, e.g. 'AXButton'.", default=""
                ),
                "double": boolean("Double-click instead of single.", default=False),
            },
            ["title", "control"],
        ),
        dangerous=True,
        preview=lambda a: (
            f"Click '{a.get('control')}' in {a.get('title')}"
        ),
    )
    def click_control(
        title: str, control: str, control_type: str = "", double: bool = False
    ) -> str:
        app = title.strip()
        try:
            elements = _snapshot(app, "name", "description", "role_description")
        except WindowError as exc:
            return str(exc)
        target = _best_match(elements, control, control_type)
        if target is None:
            return (
                f"No control named '{control}' in '{title}'. Call inspect_window to "
                "see the exact names."
            )
        # The label was read before clicking, so a click that closes a dialog or
        # relabels its button still reports success instead of tempting the
        # model to click again.
        clicks = "click theElement\n    click theElement" if double else "click theElement"
        ok, out = _act(app, target, clicks)
        if not ok:
            return f"ERROR: could not click '{control}': {out}"
        return f"Clicked {target.label} in '{title}'."

    @registry.define(
        "set_control_text",
        description=(
            "Put text into a named text field, replacing what is there. More "
            "reliable than clicking the field and typing."
        ),
        parameters=schema(
            {
                "title": string("The application name."),
                "control": string("The field's name, from inspect_window."),
                "text": string("The text to put in it."),
            },
            ["title", "control", "text"],
        ),
        dangerous=True,
        preview=lambda a: (
            f"Set '{a.get('control')}' in {a.get('title')} to: "
            f"{a.get('text', '')[:120]}"
        ),
    )
    def set_control_text(title: str, control: str, text: str) -> str:
        app = title.strip()
        try:
            elements = _snapshot(app, "name", "description", "role_description")
        except WindowError as exc:
            return str(exc)
        # A field and the label beside it often share a name; prefer the field.
        target = _best_match(elements, control, prefer=EDITABLE_ROLES)
        if target is None:
            return (
                f"No field named '{control}' in '{title}'. Call inspect_window to "
                "see the exact names."
            )
        ok, out = _act(app, target, f'set value of theElement to "{_escape(text)}"')
        if not ok:
            return f"ERROR: could not set '{control}': {out}"
        return f"Set '{control}' to {text[:80]!r}."

    @registry.define(
        "run_applescript",
        description=(
            "Run an arbitrary AppleScript and return its result. The escape hatch "
            "for Mac automation nothing else covers: controlling scriptable apps, "
            "reading Mail or Calendar, driving Finder, adjusting system settings."
        ),
        parameters=schema(
            {"script": string("The AppleScript source to run.")}, ["script"]
        ),
        dangerous=True,
        preview=lambda a: f"Run AppleScript:\n{a.get('script', '')}",
    )
    def run_applescript_tool(script: str) -> str:
        ok, out = run_applescript(script, timeout=60)
        if not ok:
            return f"ERROR: {out}"
        return truncate(out or "(script returned nothing)")
