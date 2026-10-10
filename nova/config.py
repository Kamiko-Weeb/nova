"""Configuration for Nova, loaded from the environment and an optional .env file."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Where writable state lives. A checkout keeps everything together; a packaged
# app cannot write inside its own bundle, so it uses a folder in the home
# directory instead.
if FROZEN:
    DATA_ROOT = Path.home() / ".nova"
else:
    DATA_ROOT = PROJECT_ROOT


def env_candidates() -> list[Path]:
    """Every place a .env might live, in priority order."""
    override = os.environ.get("NOVA_ENV_FILE", "").strip()
    paths = [Path(override).expanduser()] if override else []
    paths.append(DATA_ROOT / ".env")
    if PROJECT_ROOT != DATA_ROOT:
        paths.append(PROJECT_ROOT / ".env")
    paths.append(Path.home() / ".nova" / ".env")
    # De-duplicate while preserving order.
    seen: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.append(path)
    return seen


def active_env_file() -> Path | None:
    """The .env actually in use, if any."""
    for path in env_candidates():
        if path.exists():
            return path
    return None


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader. Existing environment variables always win."""
    chosen = path or active_env_file()
    if chosen is None or not chosen.exists():
        return
    for raw in chosen.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _num(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _dec(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class Config:
    # --- NVIDIA NIM ---
    api_key: str
    base_url: str
    model: str
    vision_model: str

    # --- generation ---
    temperature: float
    max_tokens: int

    # --- agent loop ---
    max_steps: int
    requests_per_minute: int

    # --- safety ---
    confirm_dangerous: bool
    allow_desktop_control: bool
    restrict_to_workspace: bool
    workspace: Path

    # --- misc ---
    shell_timeout: int
    log_dir: Path

    # --- discord ---
    discord_token: str
    discord_owner_id: int
    discord_channel_id: int

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        default_base = "https://integrate.api.nvidia.com/v1"
        return cls(
            api_key=os.environ.get("NVIDIA_API_KEY", "").strip(),
            base_url=os.environ.get("NVIDIA_BASE_URL", default_base).strip().rstrip("/"),
            model=os.environ.get(
                "NOVA_MODEL", "nvidia/nemotron-3-super-120b-a12b"
            ).strip(),
            vision_model=os.environ.get(
                "NOVA_VISION_MODEL", "meta/llama-3.2-90b-vision-instruct"
            ).strip(),
            temperature=_dec("NOVA_TEMPERATURE", 0.2),
            max_tokens=_num("NOVA_MAX_TOKENS", 2048),
            max_steps=_num("NOVA_MAX_STEPS", 25),
            requests_per_minute=_num("NOVA_RPM", 35),
            confirm_dangerous=_flag("NOVA_CONFIRM_DANGEROUS", True),
            allow_desktop_control=_flag("NOVA_ALLOW_DESKTOP_CONTROL", True),
            restrict_to_workspace=_flag("NOVA_RESTRICT_TO_WORKSPACE", False),
            workspace=Path(os.environ.get("NOVA_WORKSPACE", str(Path.home()))).expanduser(),
            shell_timeout=_num("NOVA_SHELL_TIMEOUT", 120),
            log_dir=Path(
                os.environ.get("NOVA_LOG_DIR", str(DATA_ROOT / "logs"))
            ).expanduser(),
            discord_token=os.environ.get("DISCORD_BOT_TOKEN", "").strip(),
            discord_owner_id=_num("DISCORD_OWNER_ID", 0),
            discord_channel_id=_num("DISCORD_CHANNEL_ID", 0),
        )

    # Derived locations. Defined here so every interface agrees on them.
    @property
    def memory_path(self) -> Path:
        return DATA_ROOT / "memory" / "notes.md"

    @property
    def usage_path(self) -> Path:
        return self.log_dir / "usage.json"

    @property
    def screenshot_dir(self) -> Path:
        return self.log_dir / "screenshots"

    @property
    def has_api_key(self) -> bool:
        """A placeholder is not a key. Real NVIDIA keys are long and random."""
        key = self.api_key
        if not key or not key.startswith("nvapi-"):
            return False
        body = key[len("nvapi-"):]
        placeholder_words = ("replace", "your", "xxx", "here", "paste", "example")
        if any(word in body.lower() for word in placeholder_words):
            return False
        return len(body) >= 20

    def require_api_key(self) -> None:
        if self.has_api_key:
            return
        env_path = active_env_file() or (DATA_ROOT / ".env")
        if not self.api_key:
            problem = "is not set"
        elif not self.api_key.startswith("nvapi-"):
            problem = "does not look like an NVIDIA key (it should start 'nvapi-')"
        else:
            problem = f"is still a placeholder ('{self.api_key}')"
        raise SystemExit(
            f"NVIDIA_API_KEY {problem}.\n\n"
            "Get a real key at https://build.nvidia.com, then put this line in:\n"
            f"    {env_path}\n\n"
            "    NVIDIA_API_KEY=nvapi-AbCdEf0123456789...\n\n"
            "(Create that file if it does not exist.)\n"
        )
