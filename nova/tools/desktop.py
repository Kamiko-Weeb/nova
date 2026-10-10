"""Mouse and keyboard control, so the agent can operate GUI applications.

Coordinates are in real screen pixels. `see_screen` reports the true resolution
alongside its description, because the image sent to the vision model is
downscaled and the model's sense of position is approximate: treat coordinates
from vision as estimates and verify with another screenshot after acting.
"""
from __future__ import annotations

import time

from ..config import Config
from .registry import Registry, integer, schema, string


def register(registry: Registry, cfg: Config) -> None:
    if not cfg.allow_desktop_control:
        return
    try:
        import pyautogui
    except Exception:
        return  # pyautogui missing or no display available

    pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort
    pyautogui.PAUSE = 0.05

    @registry.define(
        "screen_size",
        description="Return the primary display resolution and the cursor position.",
        parameters=schema(),
    )
    def screen_size() -> str:
        width, height = pyautogui.size()
        x, y = pyautogui.position()
        return f"Screen is {width}x{height}. Cursor is at ({x}, {y})."

    @registry.define(
        "click",
        description=(
            "Click the mouse at a screen coordinate. Call see_screen first to find out "
            "where to click, and again afterwards to confirm the result."
        ),
        parameters=schema(
            {
                "x": integer("Horizontal pixel coordinate."),
                "y": integer("Vertical pixel coordinate."),
                "button": string(
                    "Which button.", enum=["left", "right", "middle"], default="left"
                ),
                "clicks": integer("1 for a single click, 2 to double-click.", default=1),
            },
            ["x", "y"],
        ),
        dangerous=True,
        preview=lambda a: (
            f"{a.get('button', 'left')}-click x{a.get('clicks', 1)} "
            f"at ({a.get('x')}, {a.get('y')})"
        ),
    )
    def click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        width, height = pyautogui.size()
        if not (0 <= x < width and 0 <= y < height):
            return f"ERROR: ({x}, {y}) is outside the {width}x{height} screen."
        pyautogui.click(x=x, y=y, button=button, clicks=clicks, interval=0.1)
        return f"Clicked {button} button {clicks}x at ({x}, {y})."

    @registry.define(
        "move_mouse",
        description="Move the cursor without clicking. Useful to reveal hover states.",
        parameters=schema(
            {"x": integer("Horizontal pixel."), "y": integer("Vertical pixel.")},
            ["x", "y"],
        ),
    )
    def move_mouse(x: int, y: int) -> str:
        pyautogui.moveTo(x, y, duration=0.2)
        return f"Moved cursor to ({x}, {y})."

    @registry.define(
        "drag_mouse",
        description="Press the left button at one point, drag, and release at another.",
        parameters=schema(
            {
                "from_x": integer("Start horizontal pixel."),
                "from_y": integer("Start vertical pixel."),
                "to_x": integer("End horizontal pixel."),
                "to_y": integer("End vertical pixel."),
            },
            ["from_x", "from_y", "to_x", "to_y"],
        ),
        dangerous=True,
        preview=lambda a: (
            f"Drag ({a.get('from_x')}, {a.get('from_y')}) -> "
            f"({a.get('to_x')}, {a.get('to_y')})"
        ),
    )
    def drag_mouse(from_x: int, from_y: int, to_x: int, to_y: int) -> str:
        pyautogui.moveTo(from_x, from_y, duration=0.2)
        pyautogui.dragTo(to_x, to_y, duration=0.4, button="left")
        return f"Dragged from ({from_x}, {from_y}) to ({to_x}, {to_y})."

    @registry.define(
        "type_text",
        description=(
            "Type text into whatever currently has keyboard focus. Click the target "
            "field first."
        ),
        parameters=schema(
            {"text": string("The text to type.")},
            ["text"],
        ),
        dangerous=True,
        preview=lambda a: f"Type: {a.get('text', '')[:200]}",
    )
    def type_text(text: str) -> str:
        pyautogui.typewrite(text, interval=0.012)
        return f"Typed {len(text)} characters."

    @registry.define(
        "press_keys",
        description=(
            "Press a key or a key combination. Pass a single key like 'enter', 'tab', "
            "'escape', 'f5', or a combination joined by '+' like 'ctrl+s', "
            "'alt+tab', 'win+r', 'ctrl+shift+escape'."
        ),
        parameters=schema(
            {
                "keys": string("The key or combination, e.g. 'ctrl+s'."),
                "repeat": integer("How many times to press it.", default=1),
            },
            ["keys"],
        ),
        dangerous=True,
        preview=lambda a: f"Press {a.get('keys')} x{a.get('repeat', 1)}",
    )
    def press_keys(keys: str, repeat: int = 1) -> str:
        parts = [part.strip().lower() for part in keys.split("+") if part.strip()]
        if not parts:
            return "ERROR: no keys given."
        aliases = {"win": "winleft", "cmd": "command", "return": "enter", "esc": "escape"}
        parts = [aliases.get(part, part) for part in parts]
        for _ in range(max(1, repeat)):
            if len(parts) == 1:
                pyautogui.press(parts[0])
            else:
                pyautogui.hotkey(*parts)
            time.sleep(0.05)
        return f"Pressed {'+'.join(parts)} {max(1, repeat)} time(s)."

    @registry.define(
        "scroll",
        description="Scroll the wheel. Positive scrolls up, negative scrolls down.",
        parameters=schema(
            {
                "amount": integer("Wheel clicks; negative scrolls down."),
                "x": integer("Optional horizontal pixel to scroll over.", default=-1),
                "y": integer("Optional vertical pixel to scroll over.", default=-1),
            },
            ["amount"],
        ),
    )
    def scroll(amount: int, x: int = -1, y: int = -1) -> str:
        if x >= 0 and y >= 0:
            pyautogui.moveTo(x, y, duration=0.1)
        pyautogui.scroll(amount * 120)
        return f"Scrolled {amount} clicks."

    @registry.define(
        "wait",
        description=(
            "Pause for a few seconds, for example while an application finishes "
            "loading. Then take a screenshot to check on it."
        ),
        parameters=schema(
            {"seconds": integer("How long to wait (max 30).", default=2)},
        ),
    )
    def wait(seconds: int = 2) -> str:
        duration = max(0, min(30, seconds))
        time.sleep(duration)
        return f"Waited {duration} seconds."
