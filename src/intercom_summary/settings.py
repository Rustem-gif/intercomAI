"""Central configuration, loaded once from environment / .env.

Everything that needs a token, a path, or a model id reads it from here so there is a
single, documented source of truth. Import `settings` and use its attributes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional: load a local .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


# Map an Intercom region code to its REST API base URL.
INTERCOM_REGION_HOSTS = {
    "us": "https://api.intercom.io",
    "eu": "https://api.eu.intercom.io",
    "au": "https://api.au.intercom.io",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Intercom
    intercom_token: str = field(default_factory=lambda: _env("INTERCOM_ACCESS_TOKEN"))
    intercom_region: str = field(default_factory=lambda: _env("INTERCOM_REGION", "eu").lower())
    intercom_api_version: str = field(default_factory=lambda: _env("INTERCOM_API_VERSION", "2.11"))

    # QA agent — which backend grades conversations:
    #   "ollama" → local Ollama server running Qwen (default; free, no API key)
    #   "api"    → Anthropic API (needs ANTHROPIC_API_KEY)
    qa_backend: str = field(default_factory=lambda: _env("QA_BACKEND", "ollama").lower())
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    qa_model: str = field(default_factory=lambda: _env("QA_MODEL", "claude-opus-4-8"))

    # Ollama local inference backend (Qwen)
    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5:14b"))
    # How long Ollama keeps the model resident after a request. The server's own
    # OLLAMA_KEEP_ALIVE may be 0 (unload immediately) — we override it per-request so a
    # grading batch doesn't reload the (multi-GB) model between every conversation.
    ollama_keep_alive: str = field(default_factory=lambda: _env("QA_OLLAMA_KEEP_ALIVE", "30m"))
    # Optional overrides — leave at 0 to use Ollama's defaults (recommended). Setting
    # num_ctx higher avoids truncating long transcripts but slows prefill; setting
    # num_predict can change how much the model generates, so only set if you know you need it.
    ollama_num_ctx: int = field(default_factory=lambda: int(_env("QA_OLLAMA_NUM_CTX", "0") or "0"))
    ollama_num_predict: int = field(default_factory=lambda: int(_env("QA_OLLAMA_NUM_PREDICT", "0") or "0"))

    # SLA targets (seconds) used to flag slow responses in the UI and to inform the
    # grader's `efficiency` judgement. first = time to the agent's first reply;
    # followup = max acceptable gap between subsequent agent replies.
    sla_first_response_sec: int = field(default_factory=lambda: int(_env("SLA_FIRST_RESPONSE_SEC", "120") or "120"))
    sla_followup_sec: int = field(default_factory=lambda: int(_env("SLA_FOLLOWUP_SEC", "300") or "300"))

    # Slack
    slack_bot_token: str = field(default_factory=lambda: _env("SLACK_BOT_TOKEN"))
    slack_app_token: str = field(default_factory=lambda: _env("SLACK_APP_TOKEN"))
    slack_signing_secret: str = field(default_factory=lambda: _env("SLACK_SIGNING_SECRET"))

    # Web dashboard
    web_secret_key: str = field(default_factory=lambda: _env("WEB_SECRET_KEY", "change-me-in-production"))
    web_host: str = field(default_factory=lambda: _env("WEB_HOST", "127.0.0.1"))
    web_port: int = field(default_factory=lambda: int(_env("WEB_PORT", "8000")))
    web_base_url: str = field(default_factory=lambda: _env("WEB_BASE_URL", "http://127.0.0.1:8000"))

    # Paths
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", "./data/grades.db")))
    roles_path: Path = field(default_factory=lambda: Path(_env("ROLES_PATH", "./config/roles.yaml")))
    rules_path: Path = field(default_factory=lambda: Path(_env("RULES_PATH", "./rules/support_rules.md")))
    export_dir: Path = field(default_factory=lambda: Path(_env("EXPORT_DIR", "./data/exports")))
    web_users_path: Path = field(default_factory=lambda: Path(_env("WEB_USERS_PATH", "./config/web_users.yaml")))
    eval_dir: Path = field(default_factory=lambda: Path(_env("EVAL_DIR", "./data/eval")))

    @property
    def intercom_base_url(self) -> str:
        return INTERCOM_REGION_HOSTS.get(self.intercom_region, INTERCOM_REGION_HOSTS["us"])

    def require_intercom(self) -> None:
        if not self.intercom_token:
            raise RuntimeError(
                "INTERCOM_ACCESS_TOKEN is not set. Copy .env.example to .env and fill it in."
            )

    def require_anthropic(self) -> None:
        if not self.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set (needed for QA grading).")

    def require_qa(self) -> None:
        """Validate whichever QA backend is selected."""
        if self.qa_backend == "api":
            self.require_anthropic()
        elif self.qa_backend == "ollama":
            import httpx as _httpx

            try:
                _httpx.get(f"{self.ollama_base_url}/api/tags", timeout=5.0).raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"Ollama server not reachable at {self.ollama_base_url}. "
                    "Run: brew services start ollama"
                ) from exc
        else:
            raise RuntimeError(
                f"Unknown QA_BACKEND '{self.qa_backend}' (use ollama or api)."
            )

    def require_slack(self) -> None:
        missing = [
            n
            for n, v in (("SLACK_BOT_TOKEN", self.slack_bot_token), ("SLACK_APP_TOKEN", self.slack_app_token))
            if not v
        ]
        if missing:
            raise RuntimeError(f"Slack Socket Mode needs: {', '.join(missing)}")


settings = Settings()
