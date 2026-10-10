"""Discord front end.

The agent core is synchronous, so each run happens on a worker thread via
asyncio.to_thread. Approval requests come back the other way with
run_coroutine_threadsafe, which posts a pair of buttons and waits for a press.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

import discord

from ..agent import Agent, Event
from ..app import build_session
from ..approval import CallbackApprover
from ..config import Config
from ..llm import LLM
from ..memory import Memory

DISCORD_LIMIT = 1900  # leave headroom under the 2000-character cap
APPROVAL_TIMEOUT = 300.0


def chunk(text: str, size: int = DISCORD_LIMIT) -> list[str]:
    """Split on line boundaries where possible so code and lists stay readable."""
    if len(text) <= size:
        return [text]
    pieces: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            pieces.append(current)
            current = line
        else:
            current += line
    if current:
        pieces.append(current)
    return pieces


class ApprovalView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=APPROVAL_TIMEOUT)
        self.owner_id = owner_id
        self.answer: bool | str = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only Nova's owner can answer this.", ephemeral=True
            )
            return False
        return True

    async def _finish(self, interaction: discord.Interaction, value: bool | str) -> None:
        self.answer = value
        for item in self.children:
            item.disabled = True
        label = {True: "Allowed", False: "Denied", "always": "Allowed (all like this)"}
        await interaction.response.edit_message(
            content=f"**{label[value]}**", view=self
        )
        self.stop()

    @discord.ui.button(label="Allow", style=discord.ButtonStyle.success)
    async def allow(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await self._finish(interaction, True)

    @discord.ui.button(label="Allow all like this", style=discord.ButtonStyle.secondary)
    async def allow_all(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await self._finish(interaction, "always")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await self._finish(interaction, False)


@dataclass
class Session:
    agent: Agent
    lock: threading.Lock = field(default_factory=threading.Lock)


class NovaClient(discord.Client):
    def __init__(self, cfg: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.cfg = cfg
        # One LLM and one memory shared by every channel, so token counts
        # aggregate and notes stay consistent.
        self.llm = LLM(cfg)
        self.memory = Memory(cfg.memory_path)
        self.sessions: dict[int, Session] = {}

    # ---------------------------------------------------------------- events
    async def on_ready(self) -> None:
        print(f"Nova is online as {self.user}.")
        if not self.cfg.discord_owner_id:
            print(
                "WARNING: DISCORD_OWNER_ID is not set, so Nova will ignore everyone.\n"
                "Put your numeric Discord user ID in .env."
            )
        else:
            print(f"Taking orders from user ID {self.cfg.discord_owner_id}.")
        if self.cfg.discord_channel_id:
            print(f"Restricted to channel ID {self.cfg.discord_channel_id}.")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id == getattr(self.user, "id", None):
            return
        if not self.cfg.discord_owner_id or message.author.id != self.cfg.discord_owner_id:
            return
        if (
            self.cfg.discord_channel_id
            and message.channel.id != self.cfg.discord_channel_id
        ):
            return

        content = message.content.strip()
        if not content:
            return

        command = content.lower()
        if command in {"!reset", "!clear", "!new"}:
            self.sessions.pop(message.channel.id, None)
            await message.channel.send("Fresh start. Previous conversation forgotten.")
            return
        if command in {"!tools", "!help"}:
            await self._send_tool_list(message.channel)
            return
        if command in {"!usage", "!credits"}:
            total = self.llm.usage_log.read()
            await message.channel.send(
                f"**This run:** {self.llm.usage.summary()}\n"
                f"**All sessions:** {total.summary()}"
            )
            return
        if command in {"!stop", "!cancel", "!halt"}:
            existing = self.sessions.get(message.channel.id)
            if existing and existing.lock.locked():
                existing.agent.stop()
                await message.channel.send("Stopping...")
            else:
                await message.channel.send("Nothing running.")
            return
        if command in {"!memory", "!notes"}:
            notes = self.memory.notes()
            body = "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1))
            await message.channel.send(
                f"**{len(notes)} notes remembered**\n{body}" if notes
                else "Nothing remembered yet."
            )
            return

        session = self._session(message.channel)
        if session.lock.locked():
            await message.channel.send(
                "Still working on the previous instruction. Send `!stop` to interrupt."
            )
            return

        await self._handle(session, message, content)

    # --------------------------------------------------------------- helpers
    def _session(self, channel: discord.abc.Messageable) -> Session:
        channel_id = getattr(channel, "id", 0)
        existing = self.sessions.get(channel_id)
        if existing:
            return existing

        loop = asyncio.get_running_loop()
        approver = CallbackApprover(
            lambda tool, desc: self._ask_blocking(loop, channel, tool, desc)
        )
        # Discord shows progress by editing a message, so per-token streaming
        # would just burn rate limit.
        built = build_session(
            self.cfg,
            approver=approver,
            stream=False,
            llm=self.llm,
            memory=self.memory,
        )
        session = Session(agent=built.agent)
        self.sessions[channel_id] = session
        return session

    def _ask_blocking(
        self,
        loop: asyncio.AbstractEventLoop,
        channel: discord.abc.Messageable,
        tool_name: str,
        description: str,
    ) -> bool | str:
        """Called from the worker thread. Blocks until the buttons are pressed."""
        future = asyncio.run_coroutine_threadsafe(
            self._ask(channel, tool_name, description), loop
        )
        try:
            return future.result(timeout=APPROVAL_TIMEOUT + 30)
        except Exception:
            return False

    async def _ask(
        self, channel: discord.abc.Messageable, tool_name: str, description: str
    ) -> bool | str:
        view = ApprovalView(self.cfg.discord_owner_id)
        body = description if len(description) < 1200 else description[:1200] + "\n..."
        prompt = await channel.send(
            f"**Nova wants to run `{tool_name}`**\n```\n{body}\n```", view=view
        )
        timed_out = await view.wait()
        if timed_out:
            try:
                await prompt.edit(content="**Timed out - treated as denied.**", view=None)
            except discord.HTTPException:
                pass
            return False
        return view.answer

    async def _send_tool_list(self, channel: discord.abc.Messageable) -> None:
        registry = self._session(channel).agent.registry
        lines = [
            f"`{name}`{' ⚠️' if tool.dangerous else ''} - {tool.description.splitlines()[0][:110]}"
            for name, tool in sorted(registry.tools.items())
        ]
        header = f"**{len(lines)} tools available** (⚠️ asks before running)\n"
        for part in chunk(header + "\n".join(lines)):
            await channel.send(part)

    # ------------------------------------------------------------------ work
    async def _handle(
        self, session: Session, message: discord.Message, instruction: str
    ) -> None:
        loop = asyncio.get_running_loop()
        progress: list[str] = []
        status_message: discord.Message | None = None
        last_edit = 0.0

        async def push_status() -> None:
            nonlocal status_message, last_edit
            now = loop.time()
            if now - last_edit < 1.5:
                return
            last_edit = now
            text = "\n".join(progress[-8:])[:1900] or "Working..."
            try:
                if status_message is None:
                    status_message = await message.channel.send(text)
                else:
                    await status_message.edit(content=text)
            except discord.HTTPException:
                pass

        def on_event(event: Event) -> None:
            if event.kind == "tool":
                progress.append(f"⚙️ `{event.text}`")
            elif event.kind == "result":
                progress.append(f"✅ {event.text}")
            elif event.kind == "denied":
                progress.append(f"🚫 {event.text}")
            elif event.kind == "error":
                progress.append(f"❌ {event.text[:200]}")
            else:
                return
            asyncio.run_coroutine_threadsafe(push_status(), loop)

        session.agent.on_event = on_event

        def work() -> str:
            with session.lock:
                return session.agent.run(instruction)

        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(work)
            except Exception as exc:
                reply = f"Something broke: {exc}"

        if status_message is not None and progress:
            try:
                await status_message.edit(content="\n".join(progress[-8:])[:1900])
            except discord.HTTPException:
                pass

        for part in chunk(reply):
            await message.channel.send(part)


def launch(cfg: Config) -> None:
    if not cfg.discord_token:
        raise SystemExit(
            "No DISCORD_BOT_TOKEN in .env.\n"
            "Create an application at https://discord.com/developers/applications,\n"
            "add a Bot, enable the MESSAGE CONTENT INTENT, and copy its token."
        )
    if not cfg.discord_owner_id:
        raise SystemExit(
            "No DISCORD_OWNER_ID in .env.\n"
            "Nova only takes orders from you, so it needs your numeric Discord user ID.\n"
            "Enable Developer Mode in Discord, right-click your name, Copy User ID."
        )
    NovaClient(cfg).run(cfg.discord_token, log_handler=None)
