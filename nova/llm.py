"""Thin client over NVIDIA NIM's OpenAI-compatible endpoint."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from openai import APIStatusError, OpenAI

from .config import Config

_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str | None) -> str:
    """Several NIM models (Nemotron, DeepSeek-R1) emit <think> blocks. Drop them."""
    if not text:
        return ""
    return _THINK.sub("", text).strip()


class LLMError(RuntimeError):
    pass


class Cancelled(Exception):
    """Raised when the user stops a run mid-flight."""


# --------------------------------------------------------------------- shapes
# Streaming hands back deltas rather than a finished message, so we rebuild
# something with the same shape the non-streaming path returns.


@dataclass
class FunctionCall:
    name: str = ""
    arguments: str = ""


@dataclass
class ToolCall:
    id: str = ""
    function: FunctionCall = field(default_factory=FunctionCall)
    type: str = "function"


@dataclass
class AssistantMessage:
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


# ---------------------------------------------------------------------- usage


@dataclass
class Usage:
    """Token and call counters. NIM bills credits, but tokens are the proxy."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt: int, completion: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    def merge(self, other: "Usage") -> None:
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens

    def summary(self) -> str:
        def short(n: int) -> str:
            return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

        plural = "" if self.calls == 1 else "s"
        return (
            f"{self.calls} call{plural}, {short(self.total_tokens)} tokens "
            f"(in {short(self.prompt_tokens)} / out {short(self.completion_tokens)})"
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


class UsageLog:
    """Cumulative usage across sessions, kept in a small JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> Usage:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return Usage(**{k: int(raw.get(k, 0)) for k in Usage().as_dict()})
        except (OSError, ValueError, TypeError):
            return Usage()

    def add(self, session: Usage) -> Usage:
        total = self.read()
        total.merge(session)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(total.as_dict(), indent=2), encoding="utf-8")
        except OSError:
            pass
        return total


class RateLimiter:
    """Sliding-window limiter. NVIDIA's free tier allows roughly 40 requests/minute."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(1, per_minute)
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._hits and now - self._hits[0] > 60:
                    self._hits.popleft()
                if len(self._hits) < self.per_minute:
                    self._hits.append(now)
                    return
                sleep_for = 60 - (now - self._hits[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)


class LLM:
    def __init__(self, cfg: Config) -> None:
        cfg.require_api_key()
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=180.0)
        self.limiter = RateLimiter(cfg.requests_per_minute)
        self.usage = Usage()
        self.usage_log = UsageLog(cfg.usage_path)
        # Set to False once the provider rejects stream_options.
        self._supports_usage_in_stream = True

    # ------------------------------------------------------------------ core
    def _post(self, **kwargs: Any) -> Any:
        """One request, with backoff on rate limits and transient errors."""
        delay = 2.0
        last: Exception | None = None
        for attempt in range(5):
            self.limiter.wait()
            try:
                return self.client.chat.completions.create(**kwargs)
            except APIStatusError as exc:
                last = exc
                body = str(getattr(exc, "message", "") or exc)
                # Some providers reject stream_options; drop it and retry once.
                if exc.status_code == 400 and "stream_options" in body:
                    self._supports_usage_in_stream = False
                    kwargs.pop("stream_options", None)
                    continue
                retryable = exc.status_code == 429 or exc.status_code >= 500
                if not retryable or attempt == 4:
                    raise LLMError(self._explain(exc, str(kwargs.get("model", "?")))) from exc
                time.sleep(max(self._retry_after(exc), delay))
                delay *= 2
            except Exception as exc:  # network blips, timeouts
                last = exc
                if attempt == 4:
                    raise LLMError(f"Request to NIM failed: {exc}") from exc
                time.sleep(delay)
                delay *= 2
        raise LLMError(f"Request to NIM failed: {last}")

    @staticmethod
    def _retry_after(exc: APIStatusError) -> float:
        try:
            return float(exc.response.headers.get("retry-after", 0))
        except (AttributeError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def _explain(exc: APIStatusError, model: str) -> str:
        body = str(getattr(exc, "message", "") or exc)
        if exc.status_code == 401:
            return "NIM rejected the API key (401). Check NVIDIA_API_KEY in .env."
        if exc.status_code in {402, 403} and "credit" in body.lower():
            return (
                f"NIM says you are out of credits ({exc.status_code}). Generate a "
                "fresh key at https://build.nvidia.com and update NVIDIA_API_KEY "
                "in .env."
            )
        if exc.status_code == 404:
            return (
                f"Model '{model}' was not found on NIM (404).\n"
                "Run 'python main.py models' to see what your key can reach, "
                "then set NOVA_MODEL in .env."
            )
        if exc.status_code == 429:
            return "NIM rate limit hit repeatedly (429). Lower NOVA_RPM or wait a bit."
        if exc.status_code == 400 and "tool" in body.lower():
            return (
                f"Model '{model}' appears not to support tool calling.\n"
                "Pick a tool-capable model via NOVA_MODEL in .env."
            )
        return f"NIM error {exc.status_code}: {body}"

    def _record(self, usage: Any) -> None:
        if not usage:
            return
        self.usage.add(
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )

    # ------------------------------------------------------------------ chat
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Iterable[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
        on_text: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> AssistantMessage:
        """Return the assistant's turn, which may carry tool_calls.

        With stream=True, `on_text` receives content as it arrives and `cancel`
        is checked between chunks so a run can be stopped promptly.
        """
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        tool_list = list(tools or [])
        if tool_list:
            payload["tools"] = tool_list
            payload["tool_choice"] = "auto"

        if not stream:
            response = self._post(**payload)
            if not response.choices:
                raise LLMError("NIM returned no choices.")
            self._record(getattr(response, "usage", None))
            return self._adopt(response.choices[0].message)

        payload["stream"] = True
        if self._supports_usage_in_stream:
            payload["stream_options"] = {"include_usage": True}
        return self._stream(payload, on_text, cancel)

    @staticmethod
    def _adopt(message: Any) -> AssistantMessage:
        """Convert the SDK's message object into our own shape."""
        calls = [
            ToolCall(
                id=call.id,
                function=FunctionCall(
                    name=call.function.name, arguments=call.function.arguments or "{}"
                ),
            )
            for call in (getattr(message, "tool_calls", None) or [])
        ]
        return AssistantMessage(content=message.content, tool_calls=calls or None)

    def _stream(
        self,
        payload: dict[str, Any],
        on_text: Callable[[str], None] | None,
        cancel: threading.Event | None,
    ) -> AssistantMessage:
        text_parts: list[str] = []
        calls: dict[int, ToolCall] = {}
        # Reasoning models interleave <think> blocks with the real answer; only
        # forward text to the UI once we are outside one.
        inside_think = False
        pending = ""

        response = self._post(**payload)
        try:
            for chunk in response:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()

                self._record(getattr(chunk, "usage", None))
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                piece = getattr(delta, "content", None)
                if piece:
                    text_parts.append(piece)
                    if on_text:
                        pending += piece
                        pending, inside_think = self._forward(
                            pending, inside_think, on_text
                        )

                for part in getattr(delta, "tool_calls", None) or []:
                    slot = calls.setdefault(part.index, ToolCall())
                    if part.id:
                        slot.id = part.id
                    fn = getattr(part, "function", None)
                    if fn is not None:
                        if fn.name:
                            slot.function.name = fn.name
                        if fn.arguments:
                            slot.function.arguments += fn.arguments
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        # _forward holds back a short tail in case a <think> tag is split across
        # chunks. Nothing more is coming, so release it or the display loses the
        # last few characters of every answer.
        if on_text and pending and not inside_think:
            on_text(pending)

        for slot in calls.values():
            if not slot.function.arguments:
                slot.function.arguments = "{}"

        ordered = [calls[index] for index in sorted(calls)]
        return AssistantMessage(content="".join(text_parts), tool_calls=ordered or None)

    @staticmethod
    def _forward(
        buffer: str, inside_think: bool, on_text: Callable[[str], None]
    ) -> tuple[str, bool]:
        """Emit buffered text, swallowing anything inside a <think> block.

        Holds back a short tail so a tag split across chunks is not mistaken for
        content.
        """
        while buffer:
            if inside_think:
                end = buffer.lower().find("</think>")
                if end == -1:
                    keep = min(len(buffer), 8)
                    return buffer[-keep:], True
                buffer = buffer[end + len("</think>") :]
                inside_think = False
                continue
            start = buffer.lower().find("<think>")
            if start == -1:
                if len(buffer) > 8:
                    on_text(buffer[:-8])
                    return buffer[-8:], False
                return buffer, False
            if start:
                on_text(buffer[:start])
            buffer = buffer[start + len("<think>") :]
            inside_think = True
        return "", inside_think

    # ---------------------------------------------------------------- vision
    def look(self, jpeg_bytes: bytes, question: str) -> str:
        """Ask the vision model about an image.

        A separate call because NIM's vision models do not support tool calling,
        so they cannot drive the agent loop themselves.
        """
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        if len(b64) > 180_000:
            raise LLMError(
                "Image too large for an inline NIM request even after downscaling."
            )
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ],
        }
        response = self._post(
            model=self.cfg.vision_model,
            messages=[message],
            temperature=0.0,
            max_tokens=1024,
        )
        self._record(getattr(response, "usage", None))
        return strip_reasoning(response.choices[0].message.content) or "(no description)"

    # ----------------------------------------------------------------- admin
    def list_models(self) -> list[str]:
        self.limiter.wait()
        return sorted(model.id for model in self.client.models.list().data)

    def flush_usage(self) -> Usage:
        """Fold this session's usage into the cumulative log and return the total."""
        return self.usage_log.add(self.usage)
