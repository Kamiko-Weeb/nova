"""Nova - an agent that does what you tell it, on NVIDIA NIM.

Usage:
    python main.py gui         open the desktop window
    python main.py discord     run the Discord bot
    python main.py cli         talk to it in the terminal
    python main.py models      list models your NVIDIA key can reach
    python main.py doctor      check the setup
    python main.py usage       show cumulative token usage
    python main.py notes       show what Nova remembers between sessions
    python main.py do "..."    run a single instruction and exit
"""
from __future__ import annotations

import argparse
import sys

from nova.config import PROJECT_ROOT, Config, active_env_file


def cmd_gui(cfg: Config) -> int:
    from nova.interfaces import gui

    gui.launch(cfg)
    return 0


def cmd_discord(cfg: Config) -> int:
    from nova.interfaces import discord_bot

    discord_bot.launch(cfg)
    return 0


def cmd_cli(cfg: Config) -> int:
    from nova.interfaces import cli

    cli.launch(cfg)
    return 0


def cmd_models(cfg: Config) -> int:
    from nova.llm import LLM

    models = LLM(cfg).list_models()
    print(f"{len(models)} models reachable with your key:\n")

    vision_hints = (
        "vision", "-vl-", "cosmos", "paligemma", "omni", "parse", "neva", "vila",
    )
    vision = [m for m in models if any(hint in m.lower() for hint in vision_hints)]
    others = [m for m in models if m not in set(vision)]

    print("Likely vision-capable (candidates for NOVA_VISION_MODEL):")
    for name in vision:
        print(f"  {name}")
    print("\nEverything else (candidates for NOVA_MODEL - must support tool calling):")
    for name in others:
        print(f"  {name}")
    print(
        f"\nCurrently configured:\n  NOVA_MODEL        = {cfg.model}\n"
        f"  NOVA_VISION_MODEL = {cfg.vision_model}"
    )
    return 0


def cmd_doctor(cfg: Config) -> int:
    print("Nova setup check\n" + "-" * 40)
    ok = True

    print(f"python            {sys.version.split()[0]}")
    print(f"project           {PROJECT_ROOT}")
    print(f"workspace         {cfg.workspace}")
    print(f"env file          {active_env_file() or 'none - using defaults'}")

    if cfg.has_api_key:
        print(f"NVIDIA_API_KEY    set ({cfg.api_key[:8]}...{cfg.api_key[-4:]})")
    elif cfg.api_key:
        print("NVIDIA_API_KEY    still the placeholder - paste your real key in .env")
        ok = False
    else:
        print("NVIDIA_API_KEY    MISSING - put it in .env")
        ok = False

    print("\nPackages")
    required = {
        "openai": "NIM client",
        "httpx": "web requests",
        "bs4": "HTML parsing",
    }
    optional = {
        "mss": "screen capture (see_screen)",
        "PIL": "image handling (see_screen)",
        "pyautogui": "mouse and keyboard control",
        "discord": "Discord bot",
        "send2trash": "safe deletes, to the Trash or Recycle Bin",
    }
    # macOS gets precise window control from AppleScript, which needs no package.
    if sys.platform == "win32":
        optional["pywinauto"] = "precise window control (inspect_window, click_control)"
    for module, why in required.items():
        try:
            __import__(module)
            print(f"  [ok]      {module:<12} {why}")
        except ImportError:
            print(f"  [MISSING] {module:<12} {why}  <- required")
            ok = False
    for module, why in optional.items():
        try:
            __import__(module)
            print(f"  [ok]      {module:<12} {why}")
        except Exception:
            print(f"  [absent]  {module:<12} {why}  (that feature is off)")

    print("\nSafety")
    print(f"  confirm before changes   {'ON' if cfg.confirm_dangerous else 'OFF'}")
    print(f"  desktop control          {'ON' if cfg.allow_desktop_control else 'OFF'}")
    print(f"  restricted to workspace  {'ON' if cfg.restrict_to_workspace else 'OFF'}")

    if not cfg.has_api_key:
        print("\nSkipping the live API test: no usable key.")
        return 1

    print("\nTalking to NIM...")
    try:
        from nova.llm import LLM
        from nova.tools import build_registry

        llm = LLM(cfg)
        reply = llm.chat([{"role": "user", "content": "Reply with the word: ready"}])
        print(f"  text model  [ok]      {cfg.model} -> {str(reply.content)[:40]!r}")

        from nova.memory import Memory

        memory = Memory(cfg.memory_path)
        registry = build_registry(cfg, llm, memory)
        print(f"  tools       [ok]      {len(registry.tools)} registered")

        probe = llm.chat(
            [{"role": "user", "content": "List the current directory."}],
            registry.schemas(),
        )
        used = bool(getattr(probe, "tool_calls", None))
        print(
            f"  tool calls  {'[ok]' if used else '[WARN]':<9} "
            + (
                "model called a tool correctly"
                if used
                else f"{cfg.model} did not call a tool - it may not support them"
            )
        )
        ok = ok and used
    except Exception as exc:
        print(f"  [FAIL] {exc}")
        ok = False

    print("\n" + ("All good." if ok else "Problems above need fixing."))
    return 0 if ok else 1


def cmd_do(cfg: Config, instruction: str) -> int:
    from nova.app import build_session
    from nova.approval import CallbackApprover
    from nova.interfaces.cli import Printer, _ask

    printer = Printer()
    session = build_session(cfg, approver=CallbackApprover(_ask), on_event=printer)
    try:
        reply = session.agent.run(instruction)
    except KeyboardInterrupt:
        session.agent.stop()
        print("\nInterrupted.")
        return 1
    finally:
        session.finish()
    if printer.streaming:
        print()
    else:
        print(reply)
    return 0


def cmd_usage(cfg: Config) -> int:
    from nova.llm import UsageLog

    total = UsageLog(cfg.usage_path).read()
    if not total.calls:
        print("No usage recorded yet.")
        return 0
    print("Cumulative NIM usage across all sessions")
    print("-" * 44)
    print(f"  requests           {total.calls:,}")
    print(f"  prompt tokens      {total.prompt_tokens:,}")
    print(f"  completion tokens  {total.completion_tokens:,}")
    print(f"  total tokens       {total.total_tokens:,}")
    print(f"\n  log file           {cfg.log_dir / 'usage.json'}")
    print(
        "\nNVIDIA bills credits rather than tokens, so treat this as a relative\n"
        "measure. Check the remaining balance at https://build.nvidia.com."
    )
    return 0


def cmd_notes(cfg: Config) -> int:
    from nova.memory import Memory

    memory = Memory(cfg.memory_path)
    notes = memory.notes()
    if not notes:
        print("Nothing remembered yet.")
        print(f"Notes file would live at: {memory.path}")
        return 0
    print(f"{len(notes)} notes remembered\n" + "-" * 44)
    for index, note in enumerate(notes, 1):
        print(f"  {index}. {note}")
    print(f"\nEdit or delete these by hand: {memory.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nova",
        description="An agent that does what you tell it, running on NVIDIA NIM.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="open the desktop window")
    sub.add_parser("discord", help="run the Discord bot")
    sub.add_parser("cli", help="talk to it in the terminal")
    sub.add_parser("models", help="list models your NVIDIA key can reach")
    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("usage", help="show cumulative token usage")
    sub.add_parser("notes", help="show what Nova remembers between sessions")
    do = sub.add_parser("do", help="run a single instruction and exit")
    do.add_argument("instruction", nargs="+", help="what to do")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "gui":
        return cmd_gui(cfg)
    if args.command == "discord":
        return cmd_discord(cfg)
    if args.command == "cli":
        return cmd_cli(cfg)
    if args.command == "models":
        return cmd_models(cfg)
    if args.command == "doctor":
        return cmd_doctor(cfg)
    if args.command == "usage":
        return cmd_usage(cfg)
    if args.command == "notes":
        return cmd_notes(cfg)
    if args.command == "do":
        return cmd_do(cfg, " ".join(args.instruction))

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
