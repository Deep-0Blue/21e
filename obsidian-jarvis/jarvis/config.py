"""Configuration loading for Obsidian Jarvis.

Config can come from (in order of precedence):
  1. CLI arguments
  2. A YAML/JSON config file (``--config``)
  3. Environment variables
  4. Built-in defaults
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


DEFAULT_INBOX = "00-Inbox"
DEFAULT_PROJECTS = "10-Projects"
DEFAULT_STATE_DIR = ".jarvis"


@dataclass
class Config:
    """Runtime configuration for the pipeline."""

    vault: Path
    inbox: str = DEFAULT_INBOX
    projects: str = DEFAULT_PROJECTS
    state_dir: str = DEFAULT_STATE_DIR

    # LLM backend. "offline" needs no network/keys and is fully deterministic.
    llm_backend: str = "offline"
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: Optional[str] = None
    llm_temperature: float = 0.4

    # Scheduler: how often the watcher scans the inbox (seconds).
    poll_interval: int = 30

    # Safety: max ideas to process in a single watcher tick.
    max_per_tick: int = 5

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def vault_path(self) -> Path:
        return Path(self.vault).expanduser().resolve()

    @property
    def inbox_path(self) -> Path:
        return self.vault_path / self.inbox

    @property
    def projects_path(self) -> Path:
        return self.vault_path / self.projects

    @property
    def state_path(self) -> Path:
        return self.vault_path / self.state_dir

    def ensure_dirs(self) -> None:
        for p in (self.inbox_path, self.projects_path, self.state_path):
            p.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["vault"] = str(self.vault)
        # Never serialize secrets back out.
        d.pop("llm_api_key", None)
        return d


def _load_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "PyYAML is required to read YAML config files. "
                "Install it with `pip install pyyaml`, or use a .json config."
            ) from exc
        return yaml.safe_load(text) or {}
    return json.loads(text)


def load_config(
    vault: Optional[str] = None,
    config_file: Optional[str] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> Config:
    """Build a :class:`Config` from a file, env vars, and explicit overrides."""

    data: dict[str, Any] = {}

    if config_file:
        data.update(_load_file(Path(config_file).expanduser()))

    env_map = {
        "vault": "JARVIS_VAULT",
        "inbox": "JARVIS_INBOX",
        "projects": "JARVIS_PROJECTS",
        "llm_backend": "JARVIS_LLM_BACKEND",
        "llm_model": "JARVIS_LLM_MODEL",
        "llm_base_url": "JARVIS_LLM_BASE_URL",
        "llm_api_key": "JARVIS_LLM_API_KEY",
    }
    for key, env in env_map.items():
        val = os.environ.get(env)
        if val:
            data.setdefault(key, val)

    # OpenAI's conventional key, as a convenience fallback.
    if not data.get("llm_api_key") and os.environ.get("OPENAI_API_KEY"):
        data["llm_api_key"] = os.environ["OPENAI_API_KEY"]

    if vault:
        data["vault"] = vault
    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})

    if not data.get("vault"):
        raise ValueError(
            "No vault path provided. Pass --vault, set JARVIS_VAULT, "
            "or add `vault:` to your config file."
        )

    known = {f for f in Config.__dataclass_fields__ if f != "extra"}
    kwargs = {k: v for k, v in data.items() if k in known}
    extra = {k: v for k, v in data.items() if k not in known}

    if "poll_interval" in kwargs:
        kwargs["poll_interval"] = int(kwargs["poll_interval"])
    if "max_per_tick" in kwargs:
        kwargs["max_per_tick"] = int(kwargs["max_per_tick"])
    if "llm_temperature" in kwargs:
        kwargs["llm_temperature"] = float(kwargs["llm_temperature"])

    return Config(extra=extra, **kwargs)
