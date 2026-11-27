"""Optional dense-vector indexing and deterministic retrieval ablation helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from lingual_audit.contracts import (
    CONTRACT_VERSION,
    ContractValidationError,
    validate_payload,
)
from lingual_audit.evaluate import predict_from_bundle
from lingual_audit.retrieval import (
    normalize_text,
    read_index,
    retrieve_for_item,
    snapshot_hash,
)
from lingual_audit.review import lookup_citation
from lingual_audit.state import utc_now_iso

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_METRICS = (
    "evidence_recall_at_k",
    "citation_integrity_rate",
    "verdict_accuracy",
    "term_accuracy",
    "conflict_detection_rate",
    "abstention_precision",
    "false_abstention_rate",
)
_ABSTENTIONS = {"单语限制", "待核对"}


class Embedder(Protocol):
    model_name: str
    dimension: int

    def embed_texts(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]: ...


class HashedNgramEmbedder:
    """Deterministic feature-hashed Unicode token and character n-gram embeddings."""

    model_name = "hashed-ngram-v1"

    def __init__(self, dimension: int = 256) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    @staticmethod
    def _features(text: str) -> list[str]:
        normalized = " ".join(unicodedata.normalize("NFKC", text).lower().split())
        compact = normalized.replace(" ", "")
        features = [f"w:{token}" for token in _TOKEN.findall(normalized)]
        for size in (2, 3, 4):
            features.extend(
                f"c{size}:{compact[index : index + size]}"
                for index in range(max(0, len(compact) - size + 1))
            )
        return features

    def embed_texts(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        del is_query
        embeddings = []
        for text in texts:
            vector = [0.0] * self.dimension
            for feature in self._features(str(text)):
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=9).digest()
                index = int.from_bytes(digest[:8], "big") % self.dimension
                vector[index] += 1.0 if digest[8] & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in vector))
            embeddings.append([value / norm for value in vector] if norm else vector)
        return embeddings


class SentenceTransformerEmbedder:
    """Lazy optional sentence-transformers adapter with E5 prompt prefixes."""

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        self.model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "SentenceTransformerEmbedder requires the optional "
                    "'sentence-transformers' package"
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def dimension(self) -> int:
        return int(self._load().get_sentence_embedding_dimension())

    def embed_texts(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        prefix = "query: " if is_query else "passage: "
        prepared = (
            [prefix + text for text in texts] if "e5" in self.model_name.lower() else list(texts)
        )
        encoded = self._load().encode(prepared, normalize_embeddings=True)
        return [[float(value) for value in row] for row in encoded]


def populate_embeddings(db_path: Path, embedder: Embedder) -> dict[str, Any]:
    """Replace dense embedding tables atomically and record their model metadata."""
    with sqlite3.connect(db_path) as db:
        glossary_rows = db.execute(
            """SELECT glossary_id, en_term, zh_term, definition, field_code, excerpt
               FROM glossary_entries ORDER BY glossary_id"""
        ).fetchall()
        chunk_rows = db.execute(
            "SELECT chunk_id, text_verbatim FROM document_chunks ORDER BY chunk_id"
        ).fetchall()
        glossary_texts = [
            " ".join(str(value) for value in row[1:] if value) for row in glossary_rows
        ]
        chunk_texts = [str(row[1] or "") for row in chunk_rows]
        glossary_vectors = embedder.embed_texts(glossary_texts)
        chunk_vectors = embedder.embed_texts(chunk_texts)
        if any(len(vector) != embedder.dimension for vector in glossary_vectors + chunk_vectors):
            raise ValueError("Embedder returned a vector with an unexpected dimension")

        db.execute("DROP TABLE IF EXISTS glossary_embeddings")
        db.execute("DROP TABLE IF EXISTS chunk_embeddings")
        db.execute(
            "CREATE TABLE glossary_embeddings("
            "glossary_id TEXT PRIMARY KEY, embedding_json TEXT NOT NULL, model_name TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE chunk_embeddings("
            "chunk_id TEXT PRIMARY KEY, embedding_json TEXT NOT NULL, model_name TEXT NOT NULL)"
        )
        db.executemany(
            "INSERT INTO glossary_embeddings VALUES (?, ?, ?)",
            (
                (row[0], json.dumps(vector), embedder.model_name)
                for row, vector in zip(glossary_rows, glossary_vectors, strict=True)
            ),
        )
        db.executemany(
            "INSERT INTO chunk_embeddings VALUES (?, ?, ?)",
            (
                (row[0], json.dumps(vector), embedder.model_name)
                for row, vector in zip(chunk_rows, chunk_vectors, strict=True)
            ),
        )
        db.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            (("embedding_model", embedder.model_name), ("embedding_dim", str(embedder.dimension))),
        )
    return {
        "model_name": embedder.model_name,
        "dimension": embedder.dimension,
        "glossary_embeddings": len(glossary_rows),
        "chunk_embeddings": len(chunk_rows),
    }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _metrics(results: list[dict[str, Any]]) -> dict[str, float]:
    def fraction(field: str) -> float:
        values = [row[field] for row in results if row[field] is not None]
        return _rate(sum(values), len(values))

    return {
        "evidence_recall_at_k": fraction("evidence_hit"),
        "citation_integrity_rate": _rate(
            sum(r["valid_citations"] for r in results), sum(r["citation_count"] for r in results)
        ),
        "verdict_accuracy": fraction("verdict_correct"),
        "term_accuracy": fraction("term_correct"),
        "conflict_detection_rate": fraction("conflict_detected"),
        "abstention_precision": _rate(
            sum(r["abstained"] and r["must_abstain"] for r in results),
            sum(r["abstained"] for r in results),
        ),
        "false_abstention_rate": _rate(
            sum(r["abstained"] and not r["must_abstain"] for r in results),
            sum(not r["must_abstain"] for r in results),
        ),
    }


def _score_arm(
    golden: dict[str, Any],
    db_path: Path,
    split: str,
    arm_name: str,
    retrieve: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    cases = [case for case in golden["cases"] if split == "all" or case["split"] == split]
    if not cases:
        raise ValueError(f"Golden set has no cases for split {split!r}")
    results = []
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with read_index(db_path) as db:
        for case in cases:
            item = case["item"]
            bundle = retrieve(case)
            prediction = (
                {"verdict": None, "suggested_zh": None, "evidence_refs": []}
                if arm_name == "v1_naive_doc_scan"
                else predict_from_bundle(item, bundle)
            )
            candidate_docs = list(dict.fromkeys(row["doc_id"] for row in bundle["candidates"]))
            verdict = prediction["verdict"]
            term = item.get("zh_text") if verdict == "保留" else prediction.get("suggested_zh")
            acceptable = {normalize_text(value) for value in case["acceptable_zh_terms"]}
            citations = prediction["evidence_refs"]
            result = {
                "case_id": case["case_id"],
                "item_id": item["item_id"],
                "category": case["category"],
                "retrieved_doc_ids": candidate_docs,
                "evidence_hit": bool(set(case["expected_doc_ids"]).intersection(candidate_docs))
                if case["expected_doc_ids"]
                else None,
                "predicted_verdict": verdict,
                "predicted_zh": term,
                "verdict_correct": verdict in case["expected_verdicts"],
                "term_correct": normalize_text(term) in acceptable if acceptable else None,
                "conflict_detected": verdict == "来源冲突"
                if case["category"] == "same_scope_conflict"
                or "来源冲突" in case["expected_verdicts"]
                else None,
                "abstained": verdict in _ABSTENTIONS,
                "must_abstain": case["must_abstain"],
                "citation_count": len(citations),
                "valid_citations": sum(lookup_citation(db, ref) is not None for ref in citations),
            }
            results.append(result)
            categories[case["category"]].append(result)
    report = {
        "contract_version": CONTRACT_VERSION,
        "artifact_kind": "eval_report",
        "evaluated_at": utc_now_iso(),
        "dataset_version": golden["dataset_version"],
        "arm_name": arm_name,
        "split": split,
        "total_cases": len(results),
        "metrics": _metrics(results),
        "per_category": {
            name: {"total_cases": len(rows), "metrics": _metrics(rows)}
            for name, rows in sorted(categories.items())
        },
        "case_results": results,
    }
    if errors := validate_payload("eval_report", report):
        raise ContractValidationError(errors)
    return report


def _naive_bundle(db_path: Path, case: dict[str, Any], top_k: int) -> dict[str, Any]:
    item = case["item"]
    words = _TOKEN.findall(normalize_text(item.get("en_text")))
    with read_index(db_path) as db:
        rows = []
        for row in db.execute(
            "SELECT chunk_id, doc_id, doc_version, text_verbatim, excerpt_hash FROM document_chunks"
        ):
            text = normalize_text(row["text_verbatim"])
            score = sum(text.count(word) for word in words)
            if score:
                rows.append((score, row))
        candidates = [
            {
                "evidence_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "doc_version": row["doc_version"],
                "excerpt_hash": row["excerpt_hash"],
                "candidate_zh": None,
                "applicability": "out_of_scope",
                "support_type": "reference_material",
                "channels": ["naive_keyword"],
            }
            for _, row in sorted(rows, key=lambda pair: (-pair[0], pair[1]["chunk_id"]))[:top_k]
        ]
        corpus_hash = snapshot_hash(db)
    return {
        "corpus_snapshot_hash": corpus_hash,
        "candidates": candidates,
        "conflict_flags": {
            "same_scope_conflict": False,
            "regional_variant_present": False,
            "en_only_evidence": False,
            "no_authoritative_hit": True,
            "conflicting_zh_terms": [],
        },
    }


def run_three_arm_ablation(
    golden_set_path: Path,
    db_path: Path,
    *,
    split: str = "all",
    top_k: int = 8,
    policy_path: Path | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    """Evaluate naive document scan, deterministic FTS, and hybrid dense retrieval."""
    golden = json.loads(golden_set_path.read_text(encoding="utf-8"))
    if errors := validate_payload("golden_set", golden):
        raise ContractValidationError(errors)
    if split not in {"all", "dev", "holdout"}:
        raise ValueError(f"Unsupported golden set split: {split!r}")
    active_embedder = embedder or HashedNgramEmbedder()
    populate_embeddings(db_path, active_embedder)

    def controlled(case: dict[str, Any], mode: str) -> dict[str, Any]:
        item = dict(case["item"])
        if mode == "hybrid_vector":
            query = " ".join(
                filter(
                    None,
                    (
                        item.get("en_text"),
                        item.get("hierarchy_path"),
                        " ".join(item.get("sibling_labels", [])),
                    ),
                )
            )
            item["query_embedding"] = active_embedder.embed_texts([query], is_query=True)[0]
        return retrieve_for_item(
            db_path,
            item,
            item["domain_tag"],
            case["target_region"],
            case["target_locale"],
            policy_path,
            top_k,
            mode,
        )

    arms = {
        "v1_naive_doc_scan": _score_arm(
            golden,
            db_path,
            split,
            "v1_naive_doc_scan",
            lambda case: _naive_bundle(db_path, case, top_k),
        ),
        "glossary_fts": _score_arm(
            golden, db_path, split, "glossary_fts", lambda case: controlled(case, "glossary_fts")
        ),
        "hybrid_vector": _score_arm(
            golden, db_path, split, "hybrid_vector", lambda case: controlled(case, "hybrid_vector")
        ),
    }
    baseline = arms["v1_naive_doc_scan"]["metrics"]
    comparison = [
        {
            "metric": metric,
            **{name: report["metrics"][metric] for name, report in arms.items()},
            "glossary_fts_delta": arms["glossary_fts"]["metrics"][metric] - baseline[metric],
            "hybrid_vector_delta": arms["hybrid_vector"]["metrics"][metric] - baseline[metric],
        }
        for metric in _METRICS
    ]
    return {"split": split, "top_k": top_k, "arms": arms, "comparison_table": comparison}
