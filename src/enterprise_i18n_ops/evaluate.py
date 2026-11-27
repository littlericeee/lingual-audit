"""Golden-set evaluation and ablation harness interface (Phase 1 Worker 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run_golden_evaluation(
    golden_set_path: Path,
    db_path: Path,
    arm_name: str = "glossary_fts",
    split: str = "all",
    top_k: int = 8,
    policy_path: Path | None = None,
    predictions_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate retrieval and optional review predictions against golden_set_path conforming to contracts/eval.schema.json."""
    raise NotImplementedError("Phase 1 Worker 2: evaluate.run_golden_evaluation not yet implemented")
