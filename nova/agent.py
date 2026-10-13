"""The agent loop: talk to the model, run the tools it asks for, repeat."""
from __future__ import annotations

import json
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .approval import AlwaysAllow, Approver
from .config import Config
from .llm import LLM, Cancelled, LLMError, strip_reasoning
from .memory import Memory
from .tools.registry import Registry

SYSTEM_PROMPT = """\
You are Nova, an agent that operates {user_os} on behalf of its owner. You are \
not a chatbot that describes what could be done: you have real tools and you use \
them to actually do things.

The current date is {today}. The working directory is {workspace}.

How to work:
- Take the instruction and carry it out. If it needs five steps, do all five, then \
report what happened. Do not stop after one step to ask whether to continue.
- Prefer the most direct tool. Shell commands for system and application tasks, \
file tools for files, web_search then fetch_url for information you do not have, \
run_python when a few lines of code is the cleanest route.
- Verify your work. After an action that should have changed something, check that \
it did: read the file back, list the directory, look at the window again.
- When a tool returns an error, read it and adapt. Try a different approach instead \
of re-running the same call.
- You cannot run interactive programs that wait for keyboard input at a prompt. \
Pass flags that skip prompts, or drive the GUI instead.
{capabilities}
How to answer:
- Report results, not intentions. Say what you did and what the outcome was.
- Be concise. A few sentences for a simple task. No preamble, no restating the \
request back.
- If you could not finish something, say plainly which part failed and why.
- If an instruction is genuinely ambiguous in a way that changes what you would do, \
ask one short question. Otherwise pick the sensible reading and proceed.

Some tools change the state of the computer and may ask the owner for confirmation \
before running. If a call is denied, do not retry it; take it as a no, and either \
find another route or explain what you would need.
"""

# Guidance that only makes sense when the matching tools actually registered.
# Telling a model to use inspect_window when pywinauto is missing just invites
# it to hallucinate the call and waste a step.
UI_AUTOMATION_GUIDANCE = """
Working with application windows:
- For ordinary {app_kind} and dialogs, use the window tools. Call list_windows to \
see what is open, inspect_window to get the real names and positions of its \
controls, then click_control or set_control_text. This is exact, so prefer it \
over anything involving coordinates.
- read_window_text reads a window's contents without spending a vision call.
"""

APPLESCRIPT_GUIDANCE = """\
- run_applescript is the escape hatch for anything else on this Mac: scriptable \
applications, Finder, Mail, Calendar, system settings.
- If the window tools start failing with a permissions error, call \
accessibility_status and relay what it says - only the owner can grant that.
"""

VISION_WITH_UIA = """\
- Fall back to see_screen and click only where there is no control tree worth \
reading: games, canvases, image editors, and the insides of web pages. There, \
look first, act, then look again to confirm, and treat coordinates taken from a \
screenshot as estimates.
"""

VISION_ALONE = """
Working with application windows:
- Use see_screen to find out what is on screen and where things are, then click \
or type, then call see_screen again to confirm it worked. Coordinates you infer \
from a screenshot are estimates; if a click does not land, look again rather than \
guessing repeatedly.
"""

MEMORY_GUIDANCE = """
Remembering:
- When the owner tells you to remember something, states a lasting preference, or \
explains something about their setup that will matter next time, call remember. \
Do not use it for one-off details that only matter right now.
"""


@dataclass
class Event:
    """Something worth showing in the interface."""

    # "step" | "tool" | "result" | "answer" | "error" | "denied"
    # | "text" (a streamed fragment) | "stopped"
    kind: str
    text: str
    detail: str = ""


EventSink = Callable[[Event], None]


@dataclass
class Agent:
    cfg: Config
    llm: LLM
    registry: Registry
    approver: Approver = field(default_factory=AlwaysAllow)
    on_event: EventSink | None = None
    memory: Memory | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    stream: bool = True
    # Set this to interrupt a run in progress.
    cancel: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        if not self.messages:
            self.reset()

    # ----------------------------------------------------------------- setup
    def _capability_guidance(self) -> str:
        """Only describe tools that actually registered on this machine."""
        tools = self.registry.tools
        has_uia = "inspect_window" in tools
        has_vision = "see_screen" in tools

        parts: list[str] = []
        if has_uia:
            app_kind = (
                "Mac applications"
                if platform.system() == "Darwin"
                else "Windows programs"
            )
            parts.append(UI_AUTOMATION_GUIDANCE.format(app_kind=app_kind))
            if "run_applescript" in tools:
                parts.append(APPLESCRIPT_GUIDANCE)
            if has_vision:
                parts.append(VISION_WITH_UIA)
        elif has_vision:
            parts.append(VISION_ALONE)
        if "remember" in tools:
            parts.append(MEMORY_GUIDANCE)
        return "".join(parts)

    def system_prompt(self) -> str:
        prompt = SYSTEM_PROMPT.format(
            user_os=f"{platform.system()} {platform.release()}",
            today=time.strftime("%A, %d %B %Y"),
            workspace=self.cfg.workspace,
            capabilities=self._capability_guidance(),
        )
        if self.memory is not None:
            prompt += self.memory.as_prompt()
        return prompt

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt()}]

    def _emit(self, kind: str, text: str, detail: str = "") -> None:
        if self.on_event:
            self.on_event(Event(kind, text, detail))

    def stop(self) -> None:
        """Ask the current run to wind up. Safe to call from another thread."""
        self.cancel.set()

    # ------------------------------------------------------------------ loop
    def run(self, instruction: str) -> str:
        """Carry out one instruction. Returns the final reply."""
        self.cancel.clear()
        self.messages.append({"role": "user", "content": instruction})
        self._trim()

        try:
            return self._loop()
        except Cancelled:
            self._close_open_tool_calls()
            self._emit("stopped", "Stopped.")
            return "Stopped."

    def _loop(self) -> str:
        for step in range(1, self.cfg.max_steps + 1):
            self._check_cancelled()
            self._emit("step", f"Thinking (step {step}/{self.cfg.max_steps})")

            try:
                message = self.llm.chat(
                    self.messages,
                    self.registry.schemas(),
                    stream=self.stream,
                    on_text=(lambda piece: self._emit("text", piece))
                    if self.stream
                    else None,
                    cancel=self.cancel,
                )
            except Cancelled:
                raise
            except LLMError as exc:
                text = str(exc)
                self._emit("error", text)
                return text

            tool_calls = list(message.tool_calls or [])
            content = strip_reasoning(message.content)

            entry: dict[str, Any] = {"role": "assistant", "content": content or ""}
            if tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments or "{}",
                        },
                    }
                    for call in tool_calls
                ]
            self.messages.append(entry)

            if not tool_calls:
                answer = content or "(the model replied with nothing)"
                self._emit("answer", answer)
                return answer

            # When streaming, this text has already reached the interface.
            if content and not self.stream:
                self._emit("step", content)

            for call in tool_calls:
                self._run_tool_call(call)
            self._trim()

        exhausted = (
            f"Stopped after {self.cfg.max_steps} steps without finishing. "
            "Raise NOVA_MAX_STEPS in .env, or give me a smaller piece of the task."
        )
        self._emit("error", exhausted)
        return exhausted

    def _check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def _close_open_tool_calls(self) -> None:
        """Leave the transcript valid after an interruption.

        An assistant turn with tool_calls must be followed by a result for each
        one, or the next request is a 400.
        """
        last = self.messages[-1] if self.messages else None
        if not last or last.get("role") != "assistant" or not last.get("tool_calls"):
            return
        answered = {
            message.get("tool_call_id")
            for message in self.messages
            if message.get("role") == "tool"
        }
        for call in last["tool_calls"]:
            if call["id"] not in answered:
                self._reply_to_tool(call["id"], "CANCELLED: the owner stopped the run.")

    # ------------------------------------------------------------- tool call
    def _run_tool_call(self, call: Any) -> None:
        self._check_cancelled()
        name = call.function.name
        raw_args = call.function.arguments or "{}"
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
            if not isinstance(args, dict):
                raise ValueError("arguments were not a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            self._reply_to_tool(call.id, f"ERROR: could not parse arguments: {exc}")
            return

        tool = self.registry.get(name)
        if tool is None:
            self._reply_to_tool(
                call.id,
                f"ERROR: no tool named '{name}'. Available: "
                + ", ".join(sorted(self.registry.tools)),
            )
            return

        description = tool.describe_call(args)
        self._emit("tool", name, description)

        if tool.dangerous and self.cfg.confirm_dangerous:
            if not self.approver.confirm(name, description):
                # A denial during shutdown is really a cancellation.
                self._check_cancelled()
                self._emit("denied", f"{name} was declined")
                self._reply_to_tool(
                    call.id,
                    "DENIED: the owner declined this action. Do not retry it. "
                    "Either accomplish the goal another way, or stop and explain "
                    "what you needed.",
                )
                return

        self._check_cancelled()
        started = time.monotonic()
        output = self.registry.invoke(name, args)
        elapsed = time.monotonic() - started
        self._emit("result", f"{name} finished in {elapsed:.1f}s", output)
        self._reply_to_tool(call.id, output)

    def _reply_to_tool(self, call_id: str, content: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": content}
        )

    # ------------------------------------------------------------- housekeep
    def _trim(self, keep_chars: int = 120_000, keep_recent: int = 3) -> None:
        """Keep the transcript under `keep_chars` when it grows too long.

        Whole turns go first, oldest first: a user message and everything up to
        the next one, so each tool call keeps its result - an orphaned result is
        a 400 from the API. The system prompt and the current instruction always
        stay. If one long task is over the limit on its own, its older tool
        results are cut down instead, leaving the newest `keep_recent` whole.
        """
        while self._size() > keep_chars:
            users = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]
            if len(users) < 2:
                break
            del self.messages[users[0] : users[1]]

        older = [m for m in self.messages[:-keep_recent] if m.get("role") == "tool"]
        for message in older:
            if self._size() <= keep_chars:
                break
            if len(message["content"]) > 300:
                message["content"] = (
                    message["content"][:300] + "\n... [trimmed to keep the transcript short]"
                )

    def _size(self) -> int:
        return sum(len(str(message.get("content", ""))) for message in self.messages)
