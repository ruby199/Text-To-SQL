"""
pipeline/loader.py — YAML Pipeline Config Loader

Loads pipeline/config.yaml once and provides typed accessors.
Hot-reload: call PipelineConfig.reload() to re-read the file.
"""
from __future__ import annotations
import yaml
from pathlib import Path
from functools import lru_cache

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


class PipelineConfig:
    """Singleton-style config wrapper loaded from config.yaml."""

    _instance: PipelineConfig | None = None

    def __new__(cls) -> PipelineConfig:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            self.raw: dict = yaml.safe_load(f)

    def reload(self) -> None:
        self._load()
        PipelineConfig._instance = self

    # ── Model resolution ──────────────────────────────────────
    def resolve_model(self, key: str) -> str:
        """
        Resolve a model key (e.g. "supervisor", "specialist") to a real model name.
        Falls back to gpt-4o-mini if key not found.
        """
        import config as app_config
        model_map = self.raw.get("models", {})
        model_alias = model_map.get(key, "gpt-4o-mini")
        # Allow overrides from environment
        env_override = getattr(app_config, "AGENT_MODEL", None)
        if key == "supervisor" and env_override:
            return env_override
        return model_alias

    # ── Agent config ──────────────────────────────────────────
    def agent(self, name: str) -> dict:
        """Return agent config dict from YAML."""
        return self.raw.get("agents", {}).get(name, {})

    # ── Pipeline settings ──────────────────────────────────────
    @property
    def max_retries(self) -> int:
        return self.raw.get("pipeline", {}).get("max_retries", 1)

    @property
    def eval_pass_threshold(self) -> int:
        return self.raw.get("pipeline", {}).get("eval_pass_threshold", 70)

    @property
    def streaming_enabled(self) -> bool:
        return self.raw.get("pipeline", {}).get("streaming", True)

    # ── RBAC ─────────────────────────────────────────────────
    def allowed_agents_for_role(self, role: str) -> list[str]:
        rbac = self.raw.get("rbac", {})
        return [agent for agent, roles in rbac.items() if role in roles]

    # ── Graph ─────────────────────────────────────────────────
    def graph_edges(self) -> dict:
        return self.raw.get("graph", {}).get("edges", {})

    def needs_schema_discovery(self, agent_name: str) -> bool:
        trigger = self.agent("schema_discovery").get("trigger_on", [])
        return agent_name in trigger

    # ── Compliance ───────────────────────────────────────────
    @property
    def max_query_rows(self) -> int:
        return self.raw.get("compliance", {}).get("max_query_rows", 200)

    @property
    def pii_masking(self) -> bool:
        return self.raw.get("compliance", {}).get("pii_masking", True)
