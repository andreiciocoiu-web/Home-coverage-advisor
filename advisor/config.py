"""Configurare centralizata, citita din variabile de mediu (.env)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("HCA_DATA_DIR", str(BASE_DIR / "data"))))
    # Google Drive
    drive_auth_mode: str = field(default_factory=lambda: _env("HCA_DRIVE_AUTH", "service_account"))  # service_account | oauth
    google_credentials_file: str = field(default_factory=lambda: _env("HCA_GOOGLE_CREDENTIALS", ""))
    google_token_file: str = field(default_factory=lambda: _env("HCA_GOOGLE_TOKEN", ""))
    # LLM
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("HCA_LLM_MODEL", "claude-sonnet-5-5"))
    llm_max_tokens: int = field(default_factory=lambda: int(_env("HCA_LLM_MAX_TOKENS", "8000")))
    # Securitate
    encryption_key: str = field(default_factory=lambda: _env("HCA_ENCRYPTION_KEY", ""))
    users_file: str = field(default_factory=lambda: _env("HCA_USERS_FILE", str(BASE_DIR / "config" / "users.json")))
    auth_disabled: bool = field(default_factory=lambda: _env("HCA_AUTH_DISABLED", "0") == "1")
    retention_days: int = field(default_factory=lambda: int(_env("HCA_RETENTION_DAYS", "90")))
    max_file_mb: int = field(default_factory=lambda: int(_env("HCA_MAX_FILE_MB", "40")))
    # OCR
    ocr_langs: str = field(default_factory=lambda: _env("HCA_OCR_LANGS", "ron+eng"))
    ocr_min_chars_per_page: int = field(default_factory=lambda: int(_env("HCA_OCR_MIN_CHARS", "80")))
    # Retrieval
    embedding_backend: str = field(default_factory=lambda: _env("HCA_EMBEDDINGS", "auto"))  # auto | tfidf | st
    st_model: str = field(default_factory=lambda: _env("HCA_ST_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "advisor.db"

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()


def load_json_config(name: str) -> dict:
    return json.loads((BASE_DIR / "config" / name).read_text(encoding="utf-8"))
