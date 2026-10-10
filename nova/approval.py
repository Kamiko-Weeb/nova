"""Approval gate for tools that can change things.

The agent core is synchronous and interface-agnostic: it just calls
`approver.confirm(...)` and blocks. The GUI implements that with a modal dialog,
Discord with a pair of buttons.
"""
from __future__ import annotations

from typing import Callable, Protocol


class Approver(Protocol):
    def confirm(self, tool_name: str, description: str) -> bool:
        ...


class AlwaysAllow:
    """No prompting. Used when NOVA_CONFIRM_DANGEROUS=false."""

    def confirm(self, tool_name: str, description: str) -> bool:
        return True


class AlwaysDeny:
    """Useful for dry runs and tests."""

    def confirm(self, tool_name: str, description: str) -> bool:
        return False


class CallbackApprover:
    """Delegates to a callable supplied by the interface.

    Remembers per-tool 'always allow' decisions for the lifetime of the session
    so a long task does not prompt on every single shell command.
    """

    def __init__(self, ask: Callable[[str, str], bool | str]) -> None:
        self._ask = ask
        self._blanket: set[str] = set()

    def confirm(self, tool_name: str, description: str) -> bool:
        if tool_name in self._blanket:
            return True
        answer = self._ask(tool_name, description)
        if answer == "always":
            self._blanket.add(tool_name)
            return True
        return bool(answer)

    def reset(self) -> None:
        self._blanket.clear()
