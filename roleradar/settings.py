"""Where RoleRadar keeps its files, and which address the server listens on.

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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "roleradar.db"


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("ROLERADAR_DATA_DIR", PROJECT_ROOT / "data")),
        profiles_dir=Path(os.environ.get("ROLERADAR_PROFILES_DIR", PROJECT_ROOT / "profiles")),
        host=os.environ.get("ROLERADAR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ROLERADAR_PORT", "8787")),
    )
