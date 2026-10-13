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

from ..config import Config
from .registry import Registry, boolean, integer, schema, string, truncate

SCRIPT_TIMEOUT = 30
MAX_CONTROLS = 120
WALK_LIMIT = 2000  # most elements visited when walking a window's UI tree

# AppleScript handlers prepended to scripts that look inside a window.
#
# descendantsOf: `entire contents` returns nothing on recent macOS (seen on 27),
# so walk the tree breadth-first through `UI elements` instead.
#
# labelOf: a missing name comes back as `missing value`, which coerces to the
# text "missing value" rather than "". Icon-only controls - most toolbar
# buttons, many text fields - carry their label in the description instead.
AX_HELPERS = """
on labelOf(theElement)
    tell application "System Events"
        try
            set theName to name of theElement
            if theName is not missing value and theName is not "" then return theName as text
        end try
        try
            set theDescription to description of theElement
            if theDescription is not missing value then return theDescription as text
        end try
    end tell
    return ""
end labelOf

on descendantsOf(theRoot, maxCount)
    tell application "System Events"
        set found to {}
        set pending to {theRoot}
        set i to 1
        repeat while i <= (count of pending) and (count of found) < maxCount
            set node to item i of pending
            set i to i + 1
            try
                repeat with kid in (UI elements of node)
                    set end of found to contents of kid
                    set end of pending to contents of kid
                end repeat
            end try
        end repeat
        return found
    end tell
end descendantsOf
"""

# Accessibility roles worth reporting: things a person could click or read.
USEFUL_ROLES = {
    "AXButton", "AXCheckBox", "AXComboBox", "AXTextField", "AXTextArea",
    "AXMenuItem", "AXMenuButton", "AXRadioButton", "AXSlider", "AXStaticText",
    "AXLink", "AXPopUpButton", "AXTabGroup", "AXRow", "AXCell", "AXImage",
}
EDITABLE_ROLES = {"AXTextField", "AXTextArea", "AXComboBox"}


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
    permission, so it proves nothing - and the window tools below swallow the
    resulting errors, reporting "no windows" instead of "permission missing".
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
        denied = accessibility_denied()
        if denied:
            return f"ERROR: {denied}"
        app = _escape(title.strip())
        script = AX_HELPERS + f"""
        tell application "System Events"
            tell process "{app}"
                set output to ""
                try
                    set theWindow to window 1
                on error
                    return "NOWINDOW"
                end try
                repeat with element in my descendantsOf(theWindow, {WALK_LIMIT})
                    try
                        set elementRole to role of element
                        set elementName to my labelOf(element)
                        set elementPos to position of element
                        set elementSize to size of element
                        set output to output & elementRole & "|" & elementName ¬
                            & "|" & (item 1 of elementPos) & "," & (item 2 of elementPos) ¬
                            & "|" & (item 1 of elementSize) & "," & (item 2 of elementSize) ¬
                            & linefeed
                    end try
                end repeat
                return output
            end tell
        end tell
        """
        ok, out = run_applescript(script, timeout=45)
        if not ok:
            return f"ERROR: {out}"
        if out == "NOWINDOW":
            return f"'{title}' has no inspectable front window."

        rows: list[str] = []
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) != 4:
                continue
            role, name, position, size = parts
            if role not in USEFUL_ROLES:
                continue
            if not name.strip() and role not in EDITABLE_ROLES:
                continue
            try:
                left, top = (int(float(v)) for v in position.split(","))
                width, height = (int(float(v)) for v in size.split(","))
            except ValueError:
                continue
            if width <= 0 or height <= 0:
                continue
            centre = f"({left + width // 2}, {top + height // 2})"
            label = (name.strip() or "(no label)")[:70]
            rows.append(f"{len(rows) + 1:>3}. {role:<14} {label:<70} at {centre}")
            if len(rows) >= max(1, min(MAX_CONTROLS, max_items)):
                rows.append(f"... stopped at {len(rows) - 1} controls")
                break

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
        denied = accessibility_denied()
        if denied:
            return f"ERROR: {denied}"
        app = _escape(title.strip())
        script = AX_HELPERS + f"""
        tell application "System Events"
            tell process "{app}"
                set output to ""
                try
                    set theWindow to window 1
                on error
                    return "NOWINDOW"
                end try
                repeat with element in my descendantsOf(theWindow, {WALK_LIMIT})
                    try
                        if role of element is in {{"AXStaticText", "AXTextField", "AXTextArea"}} then
                            set elementValue to ""
                            try
                                set rawValue to value of element
                                if rawValue is not missing value then set elementValue to rawValue as text
                            end try
                            if elementValue is "" then
                                try
                                    set rawName to name of element
                                    if rawName is not missing value then set elementValue to rawName as text
                                end try
                            end if
                            if elementValue is not "" then
                                set output to output & elementValue & linefeed
                            end if
                        end if
                    end try
                end repeat
                return output
            end tell
        end tell
        """
        ok, out = run_applescript(script, timeout=45)
        if not ok:
            return f"ERROR: {out}"
        if out == "NOWINDOW":
            return f"'{title}' has no inspectable front window."

        seen: list[str] = []
        for line in out.splitlines():
            text = line.strip()
            if text and text not in seen:
                seen.append(text)
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
        app = _escape(title.strip())
        target = _escape(control.strip())
        role_test = (
            f' and role is "{_escape(control_type.strip())}"'
            if control_type.strip()
            else ""
        )
        # Exact name first, then a contains-match, so partial names still work.
        # The name is read before clicking: a click that closes a dialog or
        # relabels the button would otherwise turn a success into an error, and
        # the model would click again.
        script = AX_HELPERS + f"""
        tell application "System Events"
            tell process "{app}"
                set frontmost to true
                set theWindow to window 1
                set matches to (every UI element of theWindow whose name is "{target}"{role_test})
                if (count of matches) is 0 then
                    set matches to {{}}
                    repeat with element in my descendantsOf(theWindow, {WALK_LIMIT})
                        try
                            if my labelOf(element) contains "{target}" then
                                set end of matches to contents of element
                            end if
                        end try
                    end repeat
                end if
                if (count of matches) is 0 then return "NOTFOUND"
                set theControl to item 1 of matches
                set controlName to name of theControl as text
                click theControl
                {"click theControl" if double else ""}
                return "CLICKED " & controlName
            end tell
        end tell
        """
        ok, out = run_applescript(script)
        if not ok:
            return f"ERROR: could not click '{control}': {out}"
        if out == "NOTFOUND":
            return (
                f"No control named '{control}' in '{title}'. Call inspect_window to "
                "see the exact names."
            )
        return out.replace("CLICKED", "Clicked", 1) + f" in '{title}'."

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
        app = _escape(title.strip())
        target = _escape(control.strip())
        value = _escape(text)
        script = AX_HELPERS + f"""
        tell application "System Events"
            tell process "{app}"
                set frontmost to true
                set theWindow to window 1
                set matches to (every UI element of theWindow whose name is "{target}")
                if (count of matches) is 0 then
                    set matches to {{}}
                    repeat with element in my descendantsOf(theWindow, {WALK_LIMIT})
                        try
                            if my labelOf(element) contains "{target}" then
                                set end of matches to contents of element
                            end if
                        end try
                    end repeat
                end if
                if (count of matches) is 0 then return "NOTFOUND"
                set value of (item 1 of matches) to "{value}"
                return "SET"
            end tell
        end tell
        """
        ok, out = run_applescript(script)
        if not ok:
            return f"ERROR: could not set '{control}': {out}"
        if out == "NOTFOUND":
            return (
                f"No field named '{control}' in '{title}'. Call inspect_window to "
                "see the exact names."
            )
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
