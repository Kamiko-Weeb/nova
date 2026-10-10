"""Assembling a working agent.

Every front end needs the same four things wired together in the same order.
Doing it here means they cannot drift apart, and a new interface is a few lines.
"""
from __future__ import annotations

from dataclasses import dataclass

from .agent import Agent, EventSink
from .approval import AlwaysAllow, Approver
from .config import Config
from .llm import LLM
from .memory import Memory
from .tools import Registry, build_registry


@dataclass
class Session:
    """One agent and the pieces it is made of."""

    cfg: Config
    llm: LLM
    memory: Memory
    registry: Registry
    agent: Agent

    @property
    def tool_count(self) -> int:
        return len(self.registry.tools)

    def subtitle(self, separator: str = " | ") -> str:
        """The one-line description interfaces show in a header or banner.

        The default separator is ASCII because the Windows console renders a
        middle dot as a question mark; the GUI passes a nicer one.
        """
        notes = len(self.memory.notes())
        parts = [self.cfg.model.split("/")[-1], f"{self.tool_count} tools"]
        if notes:
            parts.append(f"{notes} note{'s' if notes != 1 else ''}")
        return separator.join(parts)

    def finish(self) -> None:
        """Fold this run's token usage into the cumulative log."""
        if self.llm.usage.calls:
            self.llm.flush_usage()


def build_session(
    cfg: Config,
    *,
    approver: Approver | None = None,
    on_event: EventSink | None = None,
    stream: bool = True,
    llm: LLM | None = None,
    memory: Memory | None = None,
) -> Session:
    """Create an agent ready to take instructions.

    `approver` is ignored when NOVA_CONFIRM_DANGEROUS is off, so callers do not
    have to check the setting themselves.

    Pass `llm` and `memory` to share them between sessions. Discord does this so
    that several channels report one combined token count instead of each
    tracking its own.
    """
    llm = llm or LLM(cfg)
    memory = memory or Memory(cfg.memory_path)
    registry = build_registry(cfg, llm, memory)
    agent = Agent(
        cfg=cfg,
        llm=llm,
        registry=registry,
        approver=approver if (approver and cfg.confirm_dangerous) else AlwaysAllow(),
        on_event=on_event,
        memory=memory,
        stream=stream,
    )
    return Session(cfg=cfg, llm=llm, memory=memory, registry=registry, agent=agent)
