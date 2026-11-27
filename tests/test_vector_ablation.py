from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lingual_audit.contracts import validate_payload
from lingual_audit.corpus import build_sqlite_index, ingest_corpus
from lingual_audit.retrieval import retrieve_for_item
from lingual_audit.vector import (
    HashedNgramEmbedder,
    populate_embeddings,
    run_three_arm_ablation,
)

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST = FIXTURES / "corpus" / "manifest.json"
GOLDEN = FIXTURES / "golden" / "golden_set_v0.json"
POLICY = FIXTURES / "corpus" / "authority_policy.json"


@pytest.fixture(scope="module")
def vector_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("vector-index")
    normalized = root / "normalized"
    db_path = root / "index.sqlite"
    ingest_corpus(MANIFEST, normalized)
    build_sqlite_index(normalized, db_path)
    result = populate_embeddings(db_path, HashedNgramEmbedder())
    assert result["glossary_embeddings"] > 0
    assert result["chunk_embeddings"] > 0
    return db_path


def test_hashed_embeddings_enable_dense_channel(vector_db: Path) -> None:
    embedder = HashedNgramEmbedder()
    with sqlite3.connect(vector_db) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
        assert {"glossary_embeddings", "chunk_embeddings"} <= tables

    item = {
        "item_id": "UI-TEST-001",
        "hierarchy_path": "FX > Booking > Counterparty",
        "domain_tag": "fx",
        "control_type": "label",
        "en_text": "Counterparty",
        "zh_text": "反对党",
        "sibling_labels": ["Value Date"],
    }
    item["query_embedding"] = embedder.embed_texts(["Counterparty"], is_query=True)[0]
    bundle = retrieve_for_item(vector_db, item, "fx", policy_path=POLICY, mode="hybrid_vector")
    assert bundle["retrieval_mode"] == "hybrid_vector"
    assert any("dense_vector" in candidate["channels"] for candidate in bundle["candidates"])


@pytest.mark.parametrize("split", ["dev", "holdout", "all"])
def test_three_arm_ablation_reports_valid_metrics(vector_db: Path, split: str) -> None:
    result = run_three_arm_ablation(
        GOLDEN, vector_db, split=split, policy_path=POLICY, embedder=HashedNgramEmbedder()
    )
    assert set(result["arms"]) == {"v1_naive_doc_scan", "glossary_fts", "hybrid_vector"}
    for report in result["arms"].values():
        assert validate_payload("eval_report", report) == []
    assert len(result["comparison_table"]) == 7
    for arm in ("glossary_fts", "hybrid_vector"):
        metrics = result["arms"][arm]["metrics"]
        assert metrics["citation_integrity_rate"] == 1.0
        naive = result["arms"]["v1_naive_doc_scan"]["metrics"]
        assert (
            metrics["evidence_recall_at_k"] > naive["evidence_recall_at_k"]
            or metrics["verdict_accuracy"] > naive["verdict_accuracy"]
        )


def test_populate_embeddings_handles_empty_index(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized"
    db_path = tmp_path / "empty.sqlite"
    empty_manifest = tmp_path / "manifest.json"
    empty_manifest.write_text(
        '{"contract_version":"1.0.0","artifact_kind":"corpus_manifest",'
        '"snapshot_id":"SNAP-EMPTY-001","industry_label":"general",'
        '"created_at":"2026-11-27T00:00:00Z","documents":[]}',
        encoding="utf-8",
    )
    ingest_corpus(empty_manifest, normalized)
    build_sqlite_index(normalized, db_path)
    result = populate_embeddings(db_path, HashedNgramEmbedder(dimension=32))
    assert result["glossary_embeddings"] == result["chunk_embeddings"] == 0
