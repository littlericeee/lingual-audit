"""Review packet preparation, citation verification, and concept-scoped consistency audit (Phase 1 Worker 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_review_packets(
    db_path: Path,
    records_dir: Path,
    output_path: Path,
    target_region: str = "CN_MAINLAND",
    target_locale: str = "zh-CN",
    policy_path: Path | None = None,
    mode: str = "glossary_fts",
) -> dict[str, Any]:
    """Generate controlled evidence packets for host LLM review across all page inventories in records_dir."""
    raise NotImplementedError("Phase 1 Worker 2: review.prepare_review_packets not yet implemented")


def validate_reviews_against_index(
    review_path: Path,
    db_path: Path,
    records_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify review.json schema, corpus snapshot match, every cited evidence_id/doc_version/excerpt_hash, and UI item linkage."""
    raise NotImplementedError("Phase 1 Worker 2: review.validate_reviews_against_index not yet implemented")


def check_cross_menu_consistency(review_path: Path) -> dict[str, Any]:
    """Detect same-domain / same-concept terminology drift across menus while permitting intentional polysemy."""
    raise NotImplementedError("Phase 1 Worker 2: review.check_cross_menu_consistency not yet implemented")
