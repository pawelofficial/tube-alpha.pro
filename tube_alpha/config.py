"""Centralized configuration for Tube Alpha.

All settings come from environment variables and config.yaml.
Usage:
    from tube_alpha.config import Settings
    settings = Settings()
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

import yaml
from dotenv import load_dotenv

load_dotenv()

# Project root is the parent of the tube_alpha package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SCHEMA_FILE = PROJECT_ROOT / "schema.json"
CONFIG_YAML = PROJECT_ROOT / "config.yaml"


def _load_yaml_config() -> dict:
    if CONFIG_YAML.exists():
        with open(CONFIG_YAML) as f:
            return yaml.safe_load(f) or {}
    return {}


@dataclass(frozen=True)
class Settings:
    # Environment
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development").lower())

    # Paths
    project_root: Path = PROJECT_ROOT
    data_dir: Path = DATA_DIR
    schema_file: Path = SCHEMA_FILE

    # Database paths
    data_db_path: Path = field(default_factory=lambda: DATA_DIR / "data.sqlite")
    admin_db_path: Path = field(default_factory=lambda: DATA_DIR / "admin.sqlite")

    # OpenAI
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o"))

    # Proxy
    proxy_username: str = field(default_factory=lambda: os.getenv("WEBSHARE_PROXY_USERNAME", ""))
    proxy_password: str = field(default_factory=lambda: os.getenv("WEBSHARE_PROXY_PASSWORD", ""))
    proxy_user_count: int = field(default_factory=lambda: int(os.getenv("WEBSHARE_PROXY_USER_COUNT", "5")))

    # YouTube scraping (from config.yaml)
    yt_channels: List[str] = field(default_factory=lambda: _load_yaml_config().get("YTD", {}).get("CHANNELS", []))
    yt_vids_count: int = field(default_factory=lambda: _load_yaml_config().get("YTD", {}).get("VIDS_COUNT", 10))
    yt_language: str = field(default_factory=lambda: _load_yaml_config().get("YTD", {}).get("LANGUAGE", "en"))
    quote_separator: str = field(default_factory=lambda: _load_yaml_config().get("YTD", {}).get("SEP", "|~|"))

    # Sentiment analysis
    max_tokens_per_chunk: int = 1000

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def proxy_base_username(self) -> str:
        """Base username without the -N suffix."""
        if "-" in self.proxy_username:
            return self.proxy_username.rsplit("-", 1)[0]
        return self.proxy_username
