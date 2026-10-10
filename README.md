# Nova

A bot that does what you tell it. You type an instruction in plain English; it
decides which tools to use and carries it out on your Windows machine — running
commands, handling files, searching the web, reading and driving application
windows, looking at your screen, and controlling the mouse and keyboard.

Runs on **NVIDIA NIM**, so it works with the free API keys from
[build.nvidia.com](https://build.nvidia.com).

| Command | What it does |
|---|---|
| `python main.py gui` | desktop chat window |
| `python main.py discord` | Discord bot you can message from your phone |
| `python main.py cli` | terminal REPL |
| `python main.py do "..."` | run one instruction and exit |
| `python main.py doctor` | check the setup |
| `python main.py models` | list models your key can reach |
| `python main.py usage` | cumulative token usage |
| `python main.py notes` | what Nova remembers between sessions |

---

## Setup

**1. Install the dependencies**

```bash
python -m pip install -r requirements.txt
```

**2. Get an NVIDIA key**

Sign up at [build.nvidia.com](https://build.nvidia.com) and generate an API key.
It starts with `nvapi-`. The free tier gives you 1,000 inference credits and
about 40 requests per minute.

**3. Configure**

```bash
copy .env.example .env
```

Open `.env` and put your key on line 6, replacing the placeholder entirely:

```
NVIDIA_API_KEY=nvapi-your-actual-key-here
```

**4. Check it**

```bash
python main.py doctor
```

Verifies your key, lists which optional features are available, and makes a live
call to confirm your chosen model actually supports tool calling. Fix anything it
flags before going further.

**5. Go**

```bash
python main.py gui
```

> PowerShell 5.1 has no `&&`. Chain commands with `;` instead.

---

## How it works

```
      you ──▶ GUI / Discord / CLI
                      │
                      ▼
                 Agent loop ──────────────┐
                      │                   │
            NIM text model          tool registry
         (picks the tools)      (actually does things)
                      ▲                   │
                      └── results ◀───────┘
```

The agent loops: send the conversation plus the tool list to the model, run
whatever tools it asks for, feed the results back, repeat until it answers. It
stops after `NOVA_MAX_STEPS` rounds so a confused model cannot spin forever.

**Why there are two models.** NIM's vision models cannot call tools, and its
tool-calling models cannot see images. So a text model drives the loop, and
"look at the screen" is itself a tool: it captures the display, downsizes the
image to fit NIM's inline-image limit, asks the vision model about it, and
returns a text answer the main model can reason about.

---

## The tools

Tools marked ⚠️ ask for your approval before running.

**Shell** — `run_shell` ⚠️

**Files** — `read_file` · `list_dir` · `find_files` · `search_in_files` ·
`write_file` ⚠️ · `move_path` ⚠️ · `copy_path` ⚠️ · `delete_path` ⚠️
(deletes go to the Recycle Bin by default)

**Web** — `web_search` · `fetch_url`

**Windows UI Automation** — `list_windows` · `inspect_window` ·
`read_window_text` · `focus_window` ⚠️ · `click_control` ⚠️ ·
`set_control_text` ⚠️

**Screen** — `see_screen` · `save_screenshot`

**Mouse and keyboard** — `click` ⚠️ · `type_text` ⚠️ · `press_keys` ⚠️ ·
`drag_mouse` ⚠️ · `move_mouse` · `scroll` · `screen_size` · `wait`

**Memory** — `remember` · `forget` · `list_memory`

**Escape hatch** — `run_python` ⚠️, which runs a short script and returns what
it prints. This covers tasks nobody wrote a tool for.

### Two ways to drive a GUI

Nova prefers **UI Automation** over clicking. Windows exposes a tree of real
controls with real names, so `inspect_window` returns actual buttons and text
fields with exact coordinates, and `click_control` clicks the one called "Save"
rather than a guessed pixel. For ordinary applications and dialogs this is
reliable in a way that vision is not.

Vision stays as the fallback for games, canvases, image editors, and the insides
of web pages, where there is no control tree worth reading. There, Nova is told
to look, act, then look again to confirm.

---

## Stopping it

The **Stop** button (or **Esc**) interrupts a run. It halts within a fraction of
a second: mid-sentence while the model is streaming, or before the next tool
fires. In the CLI, Ctrl+C. In Discord, `!stop`.

One limit: a tool already executing will finish. If Nova is three seconds into a
shell command, Stop prevents the *next* step rather than killing that process.

---

## Approval

By default anything that changes your computer stops and asks first, showing you
the exact command or file path:

> **Allow run_shell?**
> `Get-Process | Sort-Object CPU -Descending | Select-Object -First 3`
> `[Allow] [Allow all like this] [Deny]`

"Allow all like this" stops asking for that one tool until you restart. Denying
tells the model no and lets it try another route.

To let it run unsupervised, set `NOVA_CONFIRM_DANGEROUS=false` in `.env`. Worth
knowing what that means: the model will run shell commands and click things with
no checkpoint. It is a language model and it will sometimes be wrong. Leave
confirmation on until you trust it on the kind of task you are giving it.

Other guard rails:

- `NOVA_RESTRICT_TO_WORKSPACE=true` confines the file tools to one folder.
- `NOVA_ALLOW_DESKTOP_CONTROL=false` removes the mouse, keyboard and UI tools.
- pyautogui's failsafe: **slam the mouse into a screen corner to abort**.

---

## Memory

Nova keeps notes in `memory/notes.md` that survive restarts. Tell it to remember
something and it calls `remember`; those notes are loaded into its system prompt
at the start of every session.

```bash
python main.py notes
```

It is a plain Markdown file — open it in Notepad and edit or delete any line.
Keep it short: everything in it is sent with every request.

---

## Usage and credits

The status bar shows the running token count for the session, and totals persist
across runs:

```bash
python main.py usage
```

NVIDIA bills credits rather than tokens, so treat this as a relative measure and
check the real balance at build.nvidia.com. **The dominant cost is the tool
schemas** — all 31 definitions are sent on every single call, which is roughly
5,000 prompt tokens per step before your actual conversation. A ten-step task
costs ~50,000 prompt tokens on overhead alone. If credits get tight, a smaller
model is a much bigger saving than a shorter prompt.

---

## Discord setup

1. Create an application at
   [discord.com/developers/applications](https://discord.com/developers/applications).
2. **Bot** → add one → copy the token into `DISCORD_BOT_TOKEN`.
3. On the same page turn on the **MESSAGE CONTENT INTENT**. Without it the bot
   receives empty messages and will look broken.
4. **OAuth2 → URL Generator** → scopes `bot`, permissions *Send Messages* and
   *Read Message History*. Open the generated URL to invite it.
5. Enable Developer Mode (Settings → Advanced), right-click your own name,
   **Copy User ID**, and put that in `DISCORD_OWNER_ID`.

Nova ignores everyone except that ID — the bot runs commands on your actual PC,
so it takes orders from one person only. Set `DISCORD_CHANNEL_ID` to pin it to a
single channel.

Chat commands: `!stop` · `!reset` · `!tools` · `!usage` · `!memory`

---

## Choosing models

```bash
python main.py models
```

Lists everything your key can reach, split into likely-vision and everything
else, and shows what you currently have configured.

Defaults:

```
NOVA_MODEL=nvidia/nemotron-3-ultra-550b-a55b           # must support tool calling
NOVA_VISION_MODEL=meta/llama-3.2-90b-vision-instruct   # must accept images
```

Nemotron is NVIDIA's line built for agentic work. Lighter alternatives:
`nvidia/nemotron-3-super-120b-a12b`,
`nvidia/nemotron-3.5-lightning-30b-a3b`, or `moonshotai/kimi-k3`.

The catalogue moves, so trust `python main.py models` over any list written
down, including this one. Models named in NVIDIA's own documentation are
sometimes absent from the live API. If you pick a `NOVA_MODEL` that does not
support tool calling, Nova says so rather than silently doing nothing, and
`doctor` tests for it directly. Reasoning models that emit `<think>` blocks work
fine; the blocks are stripped, and hidden from the stream as it arrives.

---

## Tuning

| Setting | Default | What it does |
|---|---|---|
| `NOVA_MAX_STEPS` | 25 | tool rounds before it gives up on one instruction |
| `NOVA_RPM` | 35 | request throttle; stay under NVIDIA's ~40/min |
| `NOVA_TEMPERATURE` | 0.2 | low keeps it obedient rather than creative |
| `NOVA_MAX_TOKENS` | 4096 | reply length cap |
| `NOVA_SHELL_TIMEOUT` | 120 | seconds before a command is killed |
| `NOVA_WORKSPACE` | your home dir | where relative paths resolve |

---

## Things worth knowing

**It cannot answer interactive prompts.** Commands run non-interactively. If
something will ask "are you sure? [y/n]", pass the flag that skips it.

**Credits run out.** A single multi-step task can be a dozen calls, and every
`see_screen` costs an extra one. Generate a spare key before the first runs dry.

**Conversation trimming.** Long sessions drop their oldest turns to stay within
the context window. The system prompt and your notes always survive.

**UI Automation does not see everything.** It reads native Windows controls.
Electron apps, games and web page contents often expose little or nothing, and
`inspect_window` will come back thin — that is when to fall back to `see_screen`.

---

## Layout

```
main.py                    entry point and subcommands
nova/
  config.py                settings from .env, and the derived paths
  app.py                   build_session() - wires everything together
  llm.py                   NIM client, streaming, rate limiting, usage, vision
  agent.py                 the tool-calling loop, cancellation, system prompt
  approval.py              the confirmation gate
  memory.py                notes that survive restarts
  tools/
    registry.py            tool declaration, dispatch, shared formatting
    shell.py  files.py  web.py  screen.py  uia.py  desktop.py  pyexec.py
  interfaces/
    gui.py                 tkinter window
    discord_bot.py         Discord client
    cli.py                 terminal REPL
memory/notes.md            what Nova remembers
logs/usage.json            cumulative token counts
```

Every front end builds its agent the same way, so they cannot drift apart:

```python
session = build_session(cfg, approver=..., on_event=...)
session.agent.run("do the thing")
session.finish()          # folds token usage into the cumulative log
```

Pass `llm=` and `memory=` to share them between sessions — Discord does this so
several channels report one combined token count.

The system prompt is assembled from what actually registered. If pywinauto is
missing, the UI Automation guidance is left out rather than inviting the model
to call tools that do not exist.

**Adding a tool** — write a function, decorate it, done:

```python
@registry.define(
    "play_music",
    description="Start playing music in Spotify.",
    parameters=schema({"query": string("What to play.")}, ["query"]),
    dangerous=True,
)
def play_music(query: str) -> str:
    ...
    return "Playing."
```

Return a string; it goes straight back to the model. Raise whatever you like —
exceptions are caught and handed back as text so the model can recover. Mark it
`dangerous=True` if it changes something.
