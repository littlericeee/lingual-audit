"""Prepare review inputs and verify citations against their frozen corpus snapshot."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from lingual_audit.contracts import (
    CONTRACT_VERSION,
    ContractValidationError,
    validate_payload,
)
from lingual_audit.retrieval import (
    load_authority_policy,
    normalize_text,
    read_index,
    retrieve_for_page,
    snapshot_hash,
)
from lingual_audit.state import atomic_write_json


def _page_paths(records_dir: Path) -> list[Path]:
    return sorted((records_dir / "pages").glob("*.json"))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def prepare_review_packets(
    db_path: Path,
    records_dir: Path,
    output_path: Path,
    target_region: str = "CN_MAINLAND",
    target_locale: str = "zh-CN",
    policy_path: Path | None = None,
    mode: str = "glossary_fts",
) -> dict[str, Any]:
    """Write all observed items and their controlled evidence atomically."""
    policy = load_authority_policy(policy_path, target_region)
    with read_index(db_path) as db:
        snapshot = snapshot_hash(db)
    paths = _page_paths(records_dir)
    packets = []
    for path in paths:
        page = _read_json(path)
        errors = validate_payload("page_inventory", page)
        if errors:
            raise ContractValidationError(errors)
        bundles = retrieve_for_page(
            db_path,
            path,
            target_region,
            target_locale,
            policy_path,
            mode=mode,
        )
        for item, bundle in zip(page["items"], bundles, strict=True):
            if bundle["corpus_snapshot_hash"] != snapshot:
                raise ValueError("Corpus snapshot changed while preparing review packets")
            if (
                item["item_id"] != bundle["item_id"]
                or any(
                    item[field] != bundle["query"][field]
                    for field in ("en_text", "zh_text", "hierarchy_path", "control_type")
                )
                or page["domain_tag"] != bundle["query"]["domain_tag"]
            ):
                raise ValueError(f"Page inventory changed while preparing review packets: {path}")
            packets.append(
                {
                    "page_id": page["page_id"],
                    "menu_id": page["menu_id"],
                    "domain_tag": page["domain_tag"],
                    "ui_item": item,
                    "evidence_bundle": bundle,
                }
            )
    atomic_write_json(
        output_path,
        {
            "contract_version": CONTRACT_VERSION,
            "corpus_snapshot_hash": snapshot,
            "policy_version": policy["policy_version"],
            "items": packets,
        },
    )
    return {
        "page_count": len(paths),
        "item_count": len(packets),
        "output_path": str(output_path),
        "corpus_snapshot_hash": snapshot,
        "policy_version": policy["policy_version"],
    }


def lookup_citation(db: sqlite3.Connection, ref: Any) -> sqlite3.Row | None:
    """Resolve a citation by its complete identity, using support from the index."""
    columns = ("evidence_id", "doc_id", "doc_version", "excerpt_hash")
    if not isinstance(ref, dict) or not all(isinstance(ref.get(field), str) for field in columns):
        return None
    identity = tuple(ref[column] for column in columns)
    for table, key in (("glossary_entries", "glossary_id"), ("document_chunks", "chunk_id")):
        row = db.execute(
            f"""
            SELECT e.support_type, d.language_scope FROM {table} AS e
            JOIN documents AS d ON e.doc_id = d.doc_id
            WHERE e.{key} = ? AND e.doc_id = ? AND e.doc_version = ? AND e.excerpt_hash = ?
            """,
            identity,
        ).fetchone()
        if row is not None and ref.get("support_type", row["support_type"]) == row["support_type"]:
            return row
    return None


def _validation_result(
    errors: list[str],
    checked_reviews: int = 0,
    checked_citations: int = 0,
) -> dict[str, Any]:
    return {
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors,
        "checked_reviews": checked_reviews,
        "checked_citations": checked_citations,
    }


def _observations(records_dir: Path, errors: list[str]) -> dict[str, list[dict[str, Any]]]:
    observed = defaultdict(list)
    for path in _page_paths(records_dir):
        try:
            page = _read_json(path)
        except (OSError, ValueError) as exc:
            errors.append(f"Failed to read {path}: {exc}")
            continue
        page_errors = validate_payload("page_inventory", page)
        if page_errors:
            errors.extend(f"{path.name}: {error}" for error in page_errors)
            continue
        for item in page["items"]:
            observed[item["item_id"]].append(item)
    return observed


def validate_reviews_against_index(
    review_path: Path,
    db_path: Path,
    records_dir: Path | None = None,
    packets_path: Path | None = None,
) -> dict[str, Any]:
    """Verify the review schema, citation identities, and optionally the observed UI text."""
    try:
        review = _read_json(review_path)
    except (OSError, ValueError) as exc:
        return _validation_result([f"Failed to read {review_path}: {exc}"])
    errors = validate_payload("review", review)
    if errors:
        return _validation_result(errors)

    observed = _observations(records_dir, errors) if records_dir is not None else None
    if packets_path is not None and not packets_path.is_file():
        return _validation_result([f"Packets file does not exist: {packets_path}"])
    if packets_path is None and records_dir is not None:
        discovered_packets = records_dir / "packets.json"
        if discovered_packets.is_file():
            packets_path = discovered_packets

    packets = None
    allowed_citations: dict[str, set[tuple[str, str, str, str]]] | None = None
    if packets_path is not None:
        try:
            packets = _read_json(packets_path)
            allowed_citations = {}
            identity_fields = ("evidence_id", "doc_id", "doc_version", "excerpt_hash")
            for packet in packets["items"]:
                bundle = packet["evidence_bundle"]
                item_id = bundle["item_id"]
                allowed = allowed_citations.setdefault(item_id, set())
                for candidate in bundle.get("candidates", []):
                    allowed.add(tuple(candidate[field] for field in identity_fields))
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return _validation_result([f"Failed to read packets file {packets_path}: {exc}"])

    checked_citations = 0
    with read_index(db_path) as db:
        index_snapshot = snapshot_hash(db)
        if review["corpus_snapshot_hash"] != index_snapshot:
            errors.append("corpus_snapshot_hash does not match the index")
        if packets is not None and packets.get("corpus_snapshot_hash") != index_snapshot:
            errors.append("packets corpus_snapshot_hash does not match the index")
        for item in review["reviews"]:
            item_id = item["item_id"]
            verified = []
            if allowed_citations is not None and item_id not in allowed_citations:
                errors.append(f"{item_id}: item_id is missing from prepared packets")
            for ref in item["evidence_refs"]:
                checked_citations += 1
                identity = tuple(
                    ref[field]
                    for field in ("evidence_id", "doc_id", "doc_version", "excerpt_hash")
                )
                if allowed_citations is not None and identity not in allowed_citations.get(
                    item_id, set()
                ):
                    errors.append(
                        f"{item_id}: citation {ref['evidence_id']} is not in the item's prepared evidence bundle"
                    )
                citation = lookup_citation(db, ref)
                if citation is None:
                    errors.append(
                        f"{item_id}: citation {ref['evidence_id']} does not match index identity or support_type"
                    )
                else:
                    verified.append(citation)
            if item["verdict"] == "建议修改" and not verified:
                errors.append(f"{item_id}: 建议修改 requires at least one verified citation")
            elif item["verdict"] == "建议修改" and all(
                    citation["support_type"] == "concept_definition_only"
                    or citation["language_scope"] == "en_only"
                    for citation in verified
                ):
                errors.append(
                    f"{item_id}: 建议修改 requires a verified citation that is neither EN-only nor concept-definition-only"
                )

            if observed is not None:
                linked = observed.get(item_id, [])
                if len(linked) != 1:
                    reason = "ambiguous" if linked else "missing"
                    errors.append(f"{item_id}: {reason} item_id in page inventories")
                else:
                    for field in ("en_text", "zh_text"):
                        if item[field] != linked[0][field]:
                            errors.append(f"{item_id}: {field} differs from the observed text")
    return _validation_result(errors, len(review["reviews"]), checked_citations)


def _distinct_terms(items: list[tuple[dict[str, Any], str]]) -> list[str]:
    terms: dict[str, str] = {}
    for _, term in items:
        terms.setdefault(normalize_text(term), term)
    return list(terms.values())


def check_cross_menu_consistency(review_path: Path) -> dict[str, Any]:
    """Report terminology drift within a domain and polysemy across explicit domains."""
    review = _read_json(review_path)
    errors = validate_payload("review", review)
    if errors:
        raise ContractValidationError(errors)

    by_concept = defaultdict(list)
    by_english = defaultdict(list)
    for item in review["reviews"]:
        verdict = item["verdict"]
        term = item["suggested_zh"] if verdict == "建议修改" else item["zh_text"]
        if verdict in {"单语限制", "待核对"} or not normalize_text(term):
            continue
        en = normalize_text(item["en_text"])
        concept = item.get("concept_id") or en
        domain = item.get("domain_tag")
        if concept:
            by_concept[concept, domain].append((item, term))
        if en and domain:
            by_english[en].append((item, term))

    findings = []
    for (concept, domain), items in sorted(
        by_concept.items(),
        key=lambda entry: (
            entry[0][0],
            entry[0][1] or "",
        ),
    ):
        terms = _distinct_terms(items)
        if len(terms) >= 2:
            findings.append(
                {
                    "concept_key": concept,
                    "domain_tag": domain or "unspecified",
                    "distinct_zh_terms": terms,
                    "item_ids": [item["item_id"] for item, _ in items],
                    "severity": "conflict",
                }
            )
    conflict_count = len(findings)

    for en, items in sorted(by_english.items()):
        domains = sorted({item["domain_tag"] for item, _ in items})
        terms = _distinct_terms(items)
        if len(domains) >= 2 and len(terms) >= 2:
            findings.append(
                {
                    "concept_key": en,
                    "domain_tag": domains[0],
                    "distinct_zh_terms": terms,
                    "item_ids": [item["item_id"] for item, _ in items],
                    "severity": "advisory_polysemy",
                    "note": ", ".join(domains),
                }
            )
    return {
        "finding_count": len(findings),
        "conflict_count": conflict_count,
        "advisory_count": len(findings) - conflict_count,
        "findings": findings,
    }
