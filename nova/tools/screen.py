"""Screen capture plus vision-model reading.

NIM's vision models cannot call tools, so the agent cannot run on one directly.
Instead the text model calls `see_screen`, which captures the display, sends it
to the vision model, and hands back a text answer.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

from ..config import Config
from ..llm import LLM
from .registry import Registry, integer, schema, string

# NIM rejects inline images whose base64 payload is too large, so keep the JPEG
# comfortably under the limit (base64 inflates by ~4/3).
MAX_JPEG_BYTES = 120_000
MAX_WIDTH = 1280
# see_screen keeps a copy of what the vision model was shown, for debugging.
# Only the most recent are kept; files saved with save_screenshot are left alone.
KEEP_SCREENSHOTS = 20


def _encode(image, quality: int = 72) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _shrink_to_limit(image, resample) -> bytes:
    """Scale and recompress until the JPEG fits NIM's inline-image budget."""
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


def register(registry: Registry, cfg: Config, llm: LLM) -> None:
    # Imported once here: the import doubles as the availability check, and the
    # names are closed over by the tools below.
    try:
        import mss
        from PIL import Image
    except ImportError:
        return  # screen tools unavailable; agent still works without them

    shots_dir = cfg.screenshot_dir
    # mss.MSS is the current name; mss.mss is the deprecated alias.
    capture = getattr(mss, "MSS", None) or mss.mss

    def _grab(monitor: int):
        with capture() as sct:
            monitors = sct.monitors
            index = monitor if 0 < monitor < len(monitors) else 1
            raw = sct.grab(monitors[index])
        return Image.frombytes("RGB", raw.size, raw.rgb)

    @registry.define(
        "see_screen",
        description=(
            "Take a screenshot of the user's display and have a vision model answer a "
            "question about it. Use this whenever you need to know what is currently on "
            "screen: which window is focused, what a dialog says, where a button is, "
            "what an error message reads, or whether an action you took worked. Ask a "
            "specific question rather than a general one."
        ),
        parameters=schema(
            {
                "question": string(
                    "What you want to know about the screen, e.g. 'What application is "
                    "in the foreground and what buttons are visible?'"
                ),
                "monitor": integer(
                    "Which display to capture (1 = primary).", default=1
                ),
            },
            ["question"],
        ),
    )
    def see_screen(question: str, monitor: int = 1) -> str:
        image = _grab(monitor)
        width, height = image.size
        jpeg = _shrink_to_limit(image, Image.LANCZOS)

        try:
            shots_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            Path(shots_dir / f"screen-{stamp}.jpg").write_bytes(jpeg)
            # The timestamped names sort oldest first.
            for old in sorted(shots_dir.glob("screen-*.jpg"))[:-KEEP_SCREENSHOTS]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

        answer = llm.look(
            jpeg,
            "You are looking at a screenshot of a computer screen. Answer precisely "
            "and concretely. If the user asks where something is, describe its location "
            f"on screen.\n\nQuestion: {question}",
        )
        return (
            f"[screenshot of monitor {monitor}, actual resolution {width}x{height}]\n"
            f"{answer}"
        )

    @registry.define(
        "save_screenshot",
        description=(
            "Save a screenshot to a file without analysing it. Returns the file path."
        ),
        parameters=schema(
            {
                "path": string("Where to save the PNG.", default=""),
                "monitor": integer("Which display to capture (1 = primary).", default=1),
            },
        ),
    )
    def save_screenshot(path: str = "", monitor: int = 1) -> str:
        image = _grab(monitor)
        if path:
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = cfg.workspace / target
        else:
            shots_dir.mkdir(parents=True, exist_ok=True)
            target = shots_dir / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        return f"Saved screenshot ({image.width}x{image.height}) to {target}"
