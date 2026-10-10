"""Terminal REPL. Handy for testing tools without the GUI in the way."""
from __future__ import annotations

import sys

from ..agent import Event
from ..app import build_session
from ..approval import CallbackApprover
from ..config import Config

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
OFF = "\033[0m"


def _ask(tool_name: str, description: str) -> bool | str:
    print(f"\n{YELLOW}Nova wants to run {BOLD}{tool_name}{OFF}")
    for line in description.splitlines():
        print(f"{YELLOW}  | {line}{OFF}")
    while True:
        try:
            choice = input(f"{YELLOW}Allow? [y]es / [n]o / [a]lways: {OFF}")
        except (EOFError, KeyboardInterrupt):
            # Non-interactive stdin, or the user bailed out. Treat as "no".
            print(f"\n{RED}  No answer available - treating as denied.{OFF}")
            return False
        choice = choice.strip().lower()
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no", ""}:
            return False
        if choice in {"a", "always"}:
            return "always"


class Printer:
    """Renders agent events, keeping streamed text and tool lines apart."""

    def __init__(self) -> None:
        self.streaming = False

    def break_line(self) -> None:
        if self.streaming:
            print()
            self.streaming = False

    def __call__(self, event: Event) -> None:
        if event.kind == "text":
            if not self.streaming:
                print(f"{GREEN}nova ›{OFF} ", end="")
                self.streaming = True
            print(event.text, end="", flush=True)
            return
        if event.kind == "tool":
            self.break_line()
            print(f"{DIM}> {event.detail.splitlines()[0][:160]}{OFF}")
        elif event.kind == "result":
            self.break_line()
            print(f"{DIM}  {event.text}{OFF}")
        elif event.kind == "denied":
            self.break_line()
            print(f"{RED}  {event.text}{OFF}")
        elif event.kind == "stopped":
            self.break_line()
            print(f"{RED}  stopped{OFF}")
        elif event.kind == "error":
            self.break_line()
            print(f"{RED}{event.text}{OFF}")
        elif event.kind == "step" and not event.text.startswith("Thinking (step"):
            self.break_line()
            print(f"{DIM}{event.text}{OFF}")


def launch(cfg: Config) -> None:
    printer = Printer()
    session = build_session(cfg, approver=CallbackApprover(_ask), on_event=printer)
    llm, agent = session.llm, session.agent

    print(f"{BOLD}Nova{OFF} {DIM}{session.subtitle()}{OFF}")
    print(
        f"{DIM}Type an instruction. Ctrl+C stops a run, 'reset' clears the "
        f"conversation, 'exit' quits.{OFF}\n"
    )

    while True:
        try:
            instruction = input(f"{CYAN}you ›{OFF} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not instruction:
            continue
        if instruction.lower() in {"exit", "quit"}:
            break
        if instruction.lower() in {"reset", "clear", "new"}:
            agent.reset()
            print(f"{DIM}Conversation cleared.{OFF}\n")
            continue
        if instruction.lower() in {"usage", "credits"}:
            total = llm.usage_log.read()
            print(f"{DIM}  this session: {llm.usage.summary()}{OFF}")
            print(f"{DIM}  all sessions: {total.summary()}{OFF}\n")
            continue

        try:
            reply = agent.run(instruction)
        except KeyboardInterrupt:
            agent.stop()
            printer.break_line()
            print(f"\n{RED}Interrupted.{OFF}\n")
            continue
        except Exception as exc:
            printer.break_line()
            print(f"\n{RED}{exc}{OFF}\n")
            continue

        if printer.streaming:
            printer.break_line()
            print()
        else:
            print(f"\n{GREEN}nova ›{OFF} {reply}\n")
        print(f"{DIM}  {llm.usage.summary()}{OFF}\n")
        sys.stdout.flush()

    if llm.usage.calls:
        session_usage = llm.usage.summary()
        total = llm.flush_usage()
        print(f"{DIM}Session: {session_usage}  |  Lifetime: {total.summary()}{OFF}")
