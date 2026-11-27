"""Evaluate terminology retrieval and review predictions against a golden set."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from lingual_audit.contracts import (
    CONTRACT_VERSION,
    VALID_VERDICTS,
    ContractValidationError,
    validate_payload,
)
from lingual_audit.retrieval import (
    normalize_text,
    read_index,
    retrieve_for_item,
    snapshot_hash,
    supports_translation,
)
from lingual_audit.review import lookup_citation
from lingual_audit.state import utc_now_iso

_RAW_KEY = re.compile(r"[a-z_][a-z0-9_-]*(?:\.[a-z0-9_-]+){2,}", re.IGNORECASE | re.ASCII)
_ABSTENTIONS = {"单语限制", "待核对"}
_CITATION_FIELDS = ("evidence_id", "doc_id", "doc_version", "excerpt_hash")


def predict_from_bundle(item: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    """Use only directly supported translations and preserve uncertainty."""
    authoritative = [
        candidate for candidate in bundle["candidates"] if supports_translation(candidate)
    ]
    first = authoritative[0] if authoritative else None
    flags = bundle["conflict_flags"]
    term = None
    observed = normalize_text(item.get("zh_text"))
    declared_key = normalize_text(item.get("i18n_key_or_code"))
    status = item.get("zh_status")
    if flags["same_scope_conflict"]:
        verdict, citations = "来源冲突", authoritative
    elif (
        first is None
        or flags["no_authoritative_hit"]
        or flags["en_only_evidence"]
        or status in {"not_observed", "single_language_embedded", "pairing_pending"}
    ):
        verdict, citations = "待核对", bundle["candidates"][:1]
    elif (
        status == "missing_or_raw_key"
        or _RAW_KEY.fullmatch(observed)
        or (declared_key and observed == declared_key)
        or not observed
    ):
        verdict, citations = "缺译", [first]
        term = first["candidate_zh"]
    else:
        term = first["candidate_zh"]
        verdict = "保留" if normalize_text(term) == observed else "建议修改"
        citations = [first]
    return {
        "verdict": verdict,
        "suggested_zh": term,
        "evidence_refs": [
            {field: candidate[field] for field in _CITATION_FIELDS} for candidate in citations
        ],
    }


def _load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("reviews"), list):
        payload = payload["reviews"]
    if isinstance(payload, dict):
        entries = payload.items()
    elif isinstance(payload, list) and all(isinstance(entry, dict) for entry in payload):
        entries = ((entry.get("case_id") or entry.get("item_id"), entry) for entry in payload)
    else:
        raise ValueError("Predictions must be a map or list keyed by case_id or item_id")

    predictions = {}
    for key, entry in entries:
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise TypeError("Each prediction requires an identifier and an object")
        if key in predictions:
            raise ValueError(f"Duplicate prediction key: {key}")
        if entry.get("verdict") not in VALID_VERDICTS:
            raise ValueError(f"{key}: invalid prediction verdict")
        if entry.get("suggested_zh") is not None and not isinstance(entry["suggested_zh"], str):
            raise TypeError(f"{key}: suggested_zh must be a string or null")
        if not isinstance(entry.get("evidence_refs", []), list):
            raise TypeError(f"{key}: evidence_refs must be a list")
        predictions[key] = entry
    return predictions


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _metrics(results: list[dict[str, Any]]) -> dict[str, float]:
    def fraction(field: str) -> float:
        values = [result[field] for result in results if result[field] is not None]
        return _rate(sum(values), len(values))

    return {
        "evidence_recall_at_k": fraction("evidence_hit"),
        "citation_integrity_rate": _rate(
            sum(result["valid_citations"] for result in results),
            sum(result["citation_count"] for result in results),
        ),
        "verdict_accuracy": fraction("verdict_correct"),
        "term_accuracy": fraction("term_correct"),
        "conflict_detection_rate": fraction("conflict_detected"),
        "abstention_precision": _rate(
            sum(result["abstained"] and result["must_abstain"] for result in results),
            sum(result["abstained"] for result in results),
        ),
        "false_abstention_rate": _rate(
            sum(result["abstained"] and not result["must_abstain"] for result in results),
            sum(not result["must_abstain"] for result in results),
        ),
    }


def run_golden_evaluation(
    golden_set_path: Path,
    db_path: Path,
    arm_name: str = "glossary_fts",
    split: str = "all",
    top_k: int = 8,
    policy_path: Path | None = None,
    predictions_path: Path | None = None,
) -> dict[str, Any]:
    """Score evidence recall and decisions, limiting each metric to its eligible cases."""
    golden = json.loads(golden_set_path.read_text(encoding="utf-8"))
    errors = validate_payload("golden_set", golden)
    if errors:
        raise ContractValidationError(errors)
    if split not in {"all", "dev", "holdout"}:
        raise ValueError(f"Unsupported golden set split: {split!r}")
    cases = [case for case in golden["cases"] if split == "all" or case["split"] == split]
    if not cases:
        raise ValueError(f"Golden set has no cases for split {split!r}")

    predictions = _load_predictions(predictions_path) if predictions_path is not None else None
    mode = arm_name if arm_name in {"hybrid_vector", "hybrid_vector_rerank"} else "glossary_fts"
    results = []
    categories = defaultdict(list)
    with read_index(db_path) as db:
        snapshot = snapshot_hash(db)
        for case in cases:
            item = case["item"]
            bundle = retrieve_for_item(
                db_path,
                item,
                item["domain_tag"],
                case["target_region"],
                case["target_locale"],
                policy_path,
                top_k,
                mode,
            )
            if bundle["corpus_snapshot_hash"] != snapshot:
                raise ValueError("Corpus snapshot changed during evaluation")
            if predictions is None:
                prediction = predict_from_bundle(item, bundle)
            else:
                prediction = predictions.get(case["case_id"], predictions.get(item["item_id"], {}))

            candidate_docs = list(dict.fromkeys(c["doc_id"] for c in bundle["candidates"]))
            expected_docs = case["expected_doc_ids"]
            verdict = prediction.get("verdict")
            term = item["zh_text"] if verdict == "保留" else prediction.get("suggested_zh")
            acceptable = {normalize_text(value) for value in case["acceptable_zh_terms"]}
            citations = prediction.get("evidence_refs", [])
            result = {
                "case_id": case["case_id"],
                "item_id": item["item_id"],
                "category": case["category"],
                "retrieved_doc_ids": candidate_docs,
                "evidence_hit": bool(set(expected_docs).intersection(candidate_docs))
                if expected_docs
                else None,
                "predicted_verdict": verdict,
                "predicted_zh": term,
                "verdict_correct": verdict in case["expected_verdicts"],
                "term_correct": normalize_text(term) in acceptable if acceptable else None,
                "conflict_detected": (
                    verdict == "来源冲突"
                    if case["category"] == "same_scope_conflict"
                    or "来源冲突" in case["expected_verdicts"]
                    else None
                ),
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
            category: {"total_cases": len(rows), "metrics": _metrics(rows)}
            for category, rows in sorted(categories.items())
        },
        "case_results": results,
    }
    errors = validate_payload("eval_report", report)
    if errors:
        raise ContractValidationError(errors)
    return report
