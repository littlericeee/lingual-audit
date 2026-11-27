"""Domain-agnostic context-aware glossary + FTS5 + optional semantic retrieval interface (Phase 1 Worker 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def retrieve_for_item(
    db_path: Path,
    item: dict[str, Any],
    domain_tag: str,
    target_region: str = "CN_MAINLAND",
    target_locale: str = "zh-CN",
    policy_path: Path | None = None,
    top_k: int = 8,
    mode: str = "glossary_fts",
) -> dict[str, Any]:
    """Retrieve controlled, applicability-scored evidence bundle conforming to contracts/evidence.schema.json.

    Must work cleanly even on an empty SQLite knowledge base (emitting candidates=[], no_authoritative_hit=True).
    """
    raise NotImplementedError("Phase 1 Worker 2: retrieval.retrieve_for_item not yet implemented")


def retrieve_for_page(
    db_path: Path,
    page_inventory_path: Path,
    target_region: str = "CN_MAINLAND",
    target_locale: str = "zh-CN",
    policy_path: Path | None = None,
    top_k: int = 8,
    mode: str = "glossary_fts",
) -> list[dict[str, Any]]:
    """Retrieve evidence bundles for every UI item in a page_inventory JSON file."""
    raise NotImplementedError("Phase 1 Worker 2: retrieval.retrieve_for_page not yet implemented")
