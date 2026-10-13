"""Tool assembly."""
from __future__ import annotations

from ..config import Config
from ..llm import LLM
from . import desktop, documents, files, macui, pyexec, screen, shell, uia, web
from .registry import Registry, Tool

__all__ = ["Registry", "Tool", "build_registry"]


def build_registry(cfg: Config, llm: LLM, memory=None) -> Registry:
    """Assemble every tool available to the agent.

    Modules needing optional dependencies (screen capture, desktop control,
    UI Automation) register nothing when those are missing, so the agent
    degrades instead of failing to start.
    """
    registry = Registry()
    shell.register(registry, cfg)
    files.register(registry, cfg)
    web.register(registry, cfg)
    screen.register(registry, cfg, llm)
    documents.register(registry, cfg, llm)
    # Only one of these registers: uia on Windows, macui on macOS. They expose
    # the same tool names so the agent behaves identically on both.
    uia.register(registry, cfg)
    macui.register(registry, cfg)
    desktop.register(registry, cfg)
    pyexec.register(registry, cfg)
    if memory is not None:
        from ..memory import register as register_memory

        register_memory(registry, memory)
    return registry
