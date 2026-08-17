"""Configuration types for orchestration."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class OrchestratorConfig:
    resolved_model: str
    num_ctx: int
    max_predict: int
    max_iterations: int
    skills_payload: dict
    skills_catalog_path: Path | None = None
    catalog_mtime: float = 0.0


__all__ = ["OrchestratorConfig"]
