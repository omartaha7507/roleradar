"""Where RoleRadar keeps its files, which address the server listens on, and
which AI models it uses.

Everything can be overridden with environment variables, which is how the
tests point the app at a throwaway folder instead of your real data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    profiles_dir: Path
    # 127.0.0.1 means "this computer only": nobody else on your Wi-Fi can reach it.
    host: str = "127.0.0.1"
    port: int = 8787

    # Stage 3: the local model that ranks jobs (runs in the Ollama app).
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_timeout: int = 300  # seconds per job; a laptop CPU can be slow

    # Stage 5: Claude Code writes the CVs and cover letters.
    claude_command: str = "claude"
    claude_model: str = "sonnet"
    claude_timeout: int = 600  # seconds per job

    @property
    def db_path(self) -> Path:
        return self.data_dir / "roleradar.db"

    @property
    def applications_dir(self) -> Path:
        return self.data_dir / "applications"


SETTINGS_FILE = PROJECT_ROOT / "settings.env"


def read_settings_file(path: Path = SETTINGS_FILE) -> dict[str, str]:
    """settings.env: one KEY=value per line, # for comments. Easier than environment
    variables on Windows. Real environment variables still win over the file."""
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings() -> Settings:
    from_file = read_settings_file()

    def env(key: str, default: object) -> str:
        return os.environ.get(key, from_file.get(key, str(default)))

    return Settings(
        data_dir=Path(env("ROLERADAR_DATA_DIR", PROJECT_ROOT / "data")),
        profiles_dir=Path(env("ROLERADAR_PROFILES_DIR", PROJECT_ROOT / "profiles")),
        host=env("ROLERADAR_HOST", "127.0.0.1"),
        port=int(env("ROLERADAR_PORT", "8787")),
        ollama_url=env("ROLERADAR_OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=env("ROLERADAR_OLLAMA_MODEL", "llama3.1:8b"),
        ollama_timeout=int(env("ROLERADAR_OLLAMA_TIMEOUT", "300")),
        claude_command=env("ROLERADAR_CLAUDE_COMMAND", "claude"),
        claude_model=env("ROLERADAR_CLAUDE_MODEL", "sonnet"),
        claude_timeout=int(env("ROLERADAR_CLAUDE_TIMEOUT", "600")),
    )
