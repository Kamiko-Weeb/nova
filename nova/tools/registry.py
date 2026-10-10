"""Tool registry: declaration, JSON-schema export, and dispatch."""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_RESULT_CHARS = 12_000


def truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    return text[:limit] + f"\n... [truncated {cut} more characters]"


def format_process(returncode: int, stdout: str, stderr: str, empty: str) -> str:
    """Render a finished subprocess the way the model reads it best."""
    parts = [f"exit code: {returncode}"]
    out, err = stdout.strip(), stderr.strip()
    if out:
        parts.append("--- stdout ---\n" + out)
    if err:
        parts.append("--- stderr ---\n" + err)
    if not out and not err:
        parts.append(empty)
    return truncate("\n".join(parts))


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., str]
    dangerous: bool = False
    # Human-readable one-liner for the approval prompt, built from the call args.
    preview: Callable[[dict[str, Any]], str] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def describe_call(self, args: dict[str, Any]) -> str:
        if self.preview:
            try:
                return self.preview(args)
            except Exception:
                pass
        rendered = json.dumps(args, ensure_ascii=False)
        return f"{self.name}({truncate(rendered, 400)})"


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def define(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        *,
        dangerous: bool = False,
        preview: Callable[[dict[str, Any]], str] | None = None,
    ) -> Callable[[Callable[..., str]], Callable[..., str]]:
        """Decorator form of add()."""

        def wrap(fn: Callable[..., str]) -> Callable[..., str]:
            self.add(
                Tool(
                    name=name,
                    description=description,
                    parameters=parameters,
                    fn=fn,
                    dangerous=dangerous,
                    preview=preview,
                )
            )
            return fn

        return wrap

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self.tools.values()]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def invoke(self, name: str, args: dict[str, Any]) -> str:
        """Run a tool. Errors come back as text so the model can recover."""
        tool = self.tools.get(name)
        if tool is None:
            known = ", ".join(sorted(self.tools)) or "(none)"
            return f"ERROR: no tool named '{name}'. Available tools: {known}"
        try:
            result = tool.fn(**args)
        except TypeError as exc:
            return f"ERROR: bad arguments for '{name}': {exc}"
        except Exception as exc:
            detail = traceback.format_exc(limit=3)
            return f"ERROR while running '{name}': {exc}\n{detail}"
        if result is None:
            return "(tool returned nothing)"
        return truncate(result if isinstance(result, str) else str(result))


def schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Shorthand for a JSON-Schema object."""
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
    }


def string(desc: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": desc, **extra}


def integer(desc: str, **extra: Any) -> dict[str, Any]:
    return {"type": "integer", "description": desc, **extra}


def boolean(desc: str, **extra: Any) -> dict[str, Any]:
    return {"type": "boolean", "description": desc, **extra}
