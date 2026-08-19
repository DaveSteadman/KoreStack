# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Typed configuration boundary for the orchestration engine. This module keeps immutable run-time
# model limits and skill-catalog inputs together, separating prompt execution from configuration
# discovery. Callers construct one OrchestratorConfig per run and pass it through the engine rather
# than reaching into global application state.
# MARK: FUNCTIONS
# Primary types: OrchestratorConfig.
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

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
