"""Configuration types for orchestration."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class OrchestratorConfig:
    resolved_model: str
    num_ctx: int
    max_iterations: int
    skills_payload: dict
    skills_catalog_path: Path | None = None
    catalog_mtime: float = 0.0
    task_planning_enabled: bool = True
    task_plan_enforce_phase: bool = False


__all__ = ["OrchestratorConfig"]
