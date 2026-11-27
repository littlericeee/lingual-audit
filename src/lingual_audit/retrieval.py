"""Policy-scoped retrieval from the frozen SQLite corpus index."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from lingual_audit.contracts import (
    CONTRACT_VERSION,
    ContractValidationError,
    validate_payload,
)
from lingual_audit.state import default_authority_policy

_MODES = {"glossary_fts", "hybrid_vector", "hybrid_vector_rerank"}
_SCOPE_ORDER = (
    "in_scope",
    "regional_variant",
    "en_definition_only",
    "cross_domain_reference",
    "out_of_scope",
)
_WORD = re.compile(r"[^\W_]+")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af]+")
_RRF_K = 60


def normalize_text(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").lower().split())


def cjk_bigrams(value: str) -> list[str]:
    return list(
        dict.fromkeys(run[i : i + 2] for run in _HAN.findall(value) for i in range(len(run) - 1))
    )


def load_authority_policy(path: Path | None, target_region: str) -> dict[str, Any]:
    if path is None:
        return default_authority_policy(target_region=target_region)
    policy = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_payload("authority_policy", policy)
    if errors:
        raise ContractValidationError(errors)
    return policy


@contextmanager
def read_index(path: Path) -> Iterator[sqlite3.Connection]:
    """Pin all reads to one snapshot and fail if the index does not exist."""
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.create_function("normalize_text", 1, normalize_text, deterministic=True)
        db.execute("BEGIN")
        yield db


def snapshot_hash(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT value FROM metadata WHERE key = 'corpus_snapshot_hash'").fetchone()
    if row is None:
        raise ValueError("Index metadata is missing corpus_snapshot_hash")
    return str(row["value"])


def _domain_scope(domain: str, policy: dict[str, Any]) -> set[str]:
    parents = policy.get("domain_hierarchy", {})
    scope = {domain}
    pending = [domain]
    while pending:
        unseen = set(parents.get(pending.pop(), [])) - scope
        scope.update(unseen)
        pending.extend(unseen)
    return scope


def _applicability(
    row: sqlite3.Row,
    domains: set[str],
    regions: list[str],
    locale: str,
    policy: dict[str, Any],
    bilingual_term: bool,
) -> str:
    if row["support_type"] == "concept_definition_only" or (
        policy["conflict_rules"]["forbid_en_only_as_target_translation"]
        and row["language_scope"] == "en_only"
    ):
        return "en_definition_only"
    domain_matches = bool(domains.intersection(json.loads(row["domain_tags_json"])))
    region_matches = row["region_scope"] in regions and (
        row["target_locale"] is None or row["target_locale"].lower() == locale.lower()
    )
    if domain_matches and region_matches:
        return "in_scope"
    if domain_matches and bilingual_term:
        return "regional_variant"
    if region_matches:
        return "cross_domain_reference"
    return "out_of_scope"


def supports_translation(candidate: dict[str, Any]) -> bool:
    return (
        candidate["applicability"] == "in_scope"
        and candidate["support_type"] == "bilingual_equivalent"
        and bool(normalize_text(candidate.get("candidate_zh")))
    )


def _phrase(words: list[str]) -> str:
    # A quoted sequence of Unicode word tokens cannot introduce MATCH operators.
    return '"' + " ".join(words) + '"'


def _fts_ranks(
    db: sqlite3.Connection,
    table: str,
    id_column: str,
    expression: str,
) -> dict[str, int]:
    # All identifiers come from the frozen schema. The MATCH expression is bound as data.
    rows = db.execute(
        f"SELECT {id_column}, bm25({table}) AS relevance FROM {table}"
        f" WHERE {table} MATCH ? ORDER BY relevance, {id_column}",
        (expression,),
    )
    ranks: dict[str, int] = {}
    previous = None
    rank = 1
    for position, (evidence_id, score) in enumerate(rows, 1):
        if score != previous:
            rank = position
            previous = score
        ranks[evidence_id] = rank
    return ranks


def _bigram_ranks(
    db: sqlite3.Connection,
    table: str,
    key: str,
    column: str,
    bigrams: list[str],
) -> dict[str, int]:
    expression = f"{column} : ({' AND '.join(_phrase([gram]) for gram in bigrams)})"
    ranks = _fts_ranks(db, table, key, expression)
    if ranks:
        return ranks
    # The frozen schema does not pin the tokenizer. Trigram indexes cannot MATCH
    # a two-character token, but the FTS column still stores the original tokens.
    predicates = " AND ".join(
        f"instr(' ' || normalize_text({column}) || ' ', ?) > 0" for _ in bigrams
    )
    rows = db.execute(
        f"SELECT {key} FROM {table} WHERE {predicates}",
        tuple(f" {gram} " for gram in bigrams),
    )
    return {row[key]: 1 for row in rows}


def _unit_vector(values: Any) -> tuple[float, ...] | None:
    if not isinstance(values, (list, tuple)) or not values:
        return None
    try:
        vector = tuple(float(value) for value in values)
        norm = math.hypot(*vector)
    except (TypeError, ValueError, OverflowError):
        return None
    return tuple(value / norm for value in vector) if norm and math.isfinite(norm) else None


def _dense_ranks(db: sqlite3.Connection, query: Any) -> dict[tuple[str, str], int]:
    """Fuse precomputed, same-model JSON embeddings when an optional table is available."""
    unit_query = _unit_vector(query)
    if unit_query is None:
        return {}
    similarities: dict[tuple[str, str], float] = {}
    for kind, table, key in (
        ("glossary", "glossary_embeddings", "glossary_id"),
        ("chunk", "chunk_embeddings", "chunk_id"),
    ):
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if not {key, "embedding_json"} <= columns:
            continue
        for evidence_id, raw_vector in db.execute(f"SELECT {key}, embedding_json FROM {table}"):
            try:
                vector = _unit_vector(json.loads(raw_vector))
            except (TypeError, ValueError, UnicodeDecodeError):
                continue
            if vector is not None and len(vector) == len(unit_query):
                similarity = sum(
                    left * right for left, right in zip(unit_query, vector, strict=True)
                )
                if similarity > 0:
                    similarities[kind, evidence_id] = similarity
    ordered = sorted(similarities, key=lambda key: (-similarities[key], key))
    return {key: rank for rank, key in enumerate(ordered, 1)}


def _recall(
    db: sqlite3.Connection,
    item: dict[str, Any],
    context: list[str],
    mode: str,
) -> tuple[
    dict[tuple[str, str], dict[str, int]], str, dict[tuple[str, str], int], set[tuple[str, str]]
]:
    hits: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    en = normalize_text(item.get("en_text"))
    code = normalize_text(item.get("i18n_key_or_code"))
    words = _WORD.findall(en)
    if en or code:
        for row in db.execute(
            """
            SELECT glossary_id, normalize_text(en_normalized) = ? AS exact
            FROM glossary_entries
            WHERE (? != '' AND normalize_text(en_normalized) = ?)
               OR (? != '' AND normalize_text(field_code) = ?)
               OR (? != '' AND normalize_text(field_code) = ?)
               OR EXISTS (
                   SELECT 1 FROM json_each(en_aliases_json)
                   WHERE ? != '' AND normalize_text(value) = ?
               )
            """,
            (en, en, en, en, en, code, code, en, en),
        ):
            channel = "exact_glossary" if row["exact"] and en else "alias_glossary"
            hits["glossary", row["glossary_id"]][channel] = 1

    zh = normalize_text(item.get("zh_text"))
    primary_bigrams = cjk_bigrams(en if en else zh)
    secondary_bigrams = cjk_bigrams(zh) if en else []
    contextual_ranks = {}
    secondary_hits = set()
    context_phrases = list(
        dict.fromkeys(_phrase(tokens) for label in context if (tokens := _WORD.findall(label)))
    )
    for kind, table, key, text_columns, context_columns, bigram_column in (
        (
            "glossary",
            "glossary_fts",
            "glossary_id",
            "{en_normalized aliases_normalized}",
            "{en_normalized aliases_normalized definition_normalized}",
            "zh_bigrams",
        ),
        ("chunk", "chunks_fts", "chunk_id", "text_normalized", "text_normalized", "cjk_bigrams"),
    ):
        if words:
            expression = f"{text_columns} : {_phrase(words)}"
            for evidence_id, rank in _fts_ranks(db, table, key, expression).items():
                hits[kind, evidence_id]["fts_bm25"] = rank
            if context_phrases:
                contextual_match = (
                    f"{expression} AND {context_columns} : ({' OR '.join(context_phrases)})"
                )
                contextual_ranks.update(
                    ((kind, eid), rank)
                    for eid, rank in _fts_ranks(db, table, key, contextual_match).items()
                )
        if primary_bigrams:
            for evidence_id, rank in _bigram_ranks(
                db, table, key, bigram_column, primary_bigrams
            ).items():
                hits[kind, evidence_id]["cjk_bigram"] = rank
        if secondary_bigrams and any(hit_kind == kind for hit_kind, _ in hits):
            expression = (
                f"{bigram_column} : ({' AND '.join(_phrase([g]) for g in secondary_bigrams)})"
            )
            secondary_hits.update((kind, eid) for eid in _fts_ranks(db, table, key, expression))

    dense = _dense_ranks(db, item.get("query_embedding")) if mode != "glossary_fts" else {}
    for hit, rank in dense.items():
        hits[hit]["dense_vector"] = rank
    return hits, mode if dense else "glossary_fts", contextual_ranks, secondary_hits


def _source_rows(
    db: sqlite3.Connection,
    kind: str,
    ids: list[str],
) -> Iterator[sqlite3.Row]:
    table, key, text = (
        (
            "glossary_entries",
            "glossary_id",
            (
                "en_term, zh_term, definition, field_code, excerpt,"
                " pairing_verified, NULL AS heading_path_json"
            ),
        )
        if kind == "glossary"
        else (
            "document_chunks",
            "chunk_id",
            (
                "NULL AS en_term, NULL AS zh_term, NULL AS definition,"
                " NULL AS field_code, text_verbatim AS excerpt, 0 AS pairing_verified, heading_path_json"
            ),
        )
    )
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        placeholders = ",".join("?" for _ in batch)
        yield from db.execute(
            f"""
            SELECT e.{key} AS evidence_id, e.doc_id, e.doc_version, e.domain_tags_json,
                   e.target_locale, e.region_scope, e.normative_level, e.support_type,
                   e.locator_json, e.excerpt_hash,
                   d.publisher, d.title, d.short_citation, d.language_scope, d.canonical_group,
                   {text}
            FROM {table} AS e JOIN documents AS d ON d.doc_id = e.doc_id
            WHERE e.{key} IN ({placeholders})
            """,
            batch,
        )


def _rank_candidates(
    db: sqlite3.Connection,
    item: dict[str, Any],
    query: dict[str, Any],
    policy: dict[str, Any],
    mode: str,
) -> tuple[list[dict[str, Any]], str]:
    domain = query["domain_tag"]
    domains = _domain_scope(domain, policy)
    region = query["target_region"]
    regions = policy["region_fallback_order"].get(region, [region])
    precedence = policy["normative_precedence"]
    observed_labels = {normalize_text(query[field]) for field in ("en_text", "zh_text")}
    context_labels = [
        label
        for value in [
            *query["hierarchy_path"].split(">"),
            *query.get("sibling_labels", []),
            domain,
        ]
        if (label := normalize_text(value)) and label not in observed_labels
    ]
    hits, effective_mode, contextual_ranks, secondary_hits = _recall(db, item, context_labels, mode)
    context = " ".join(context_labels)
    context_words = set(_WORD.findall(context)) | set(cjk_bigrams(context))
    context_words.difference_update(_WORD.findall(normalize_text(query["en_text"])))
    ranked = []
    for kind in ("glossary", "chunk"):
        ids = [evidence_id for source_kind, evidence_id in hits if source_kind == kind]
        for row in _source_rows(db, kind, ids):
            if not row["excerpt"]:
                continue
            evidence_id = row["evidence_id"]
            zh = row["zh_term"] if row["pairing_verified"] else None
            scope = _applicability(
                row,
                domains,
                regions,
                query["target_locale"],
                policy,
                row["support_type"] == "bilingual_equivalent"
                and (bool(normalize_text(zh)) or kind == "chunk"),
            )
            if (
                scope == "regional_variant"
                and not policy["conflict_rules"]["preserve_regional_variants"]
            ):
                continue
            ranks = hits[kind, evidence_id]
            score = sum(1 / (_RRF_K + rank) for rank in ranks.values())
            source_context = normalize_text(
                " ".join(
                    filter(
                        None,
                        (
                            row["en_term"],
                            row["definition"],
                            row["field_code"],
                            row["excerpt"],
                            " ".join(json.loads(row["heading_path_json"] or "[]")),
                        ),
                    )
                )
            )
            overlap = len(
                context_words.intersection(
                    set(_WORD.findall(source_context)) | set(cjk_bigrams(source_context))
                )
            )
            tier = row["normative_level"]
            sort_key = (
                _SCOPE_ORDER.index(scope),
                precedence.index(tier) if tier in precedence else len(precedence),
                -score,
                "exact_glossary" not in ranks,
                -overlap,
                contextual_ranks.get((kind, evidence_id), math.inf),
                (kind, evidence_id) not in secondary_hits,
                kind,
                evidence_id,
            )
            candidate = {
                key: row[key]
                for key in (
                    "evidence_id",
                    "doc_id",
                    "doc_version",
                    "publisher",
                    "title",
                    "short_citation",
                    "canonical_group",
                    "region_scope",
                    "target_locale",
                    "normative_level",
                    "support_type",
                    "excerpt",
                    "excerpt_hash",
                )
            }
            candidate.update(
                source_kind=kind,
                applicability=scope,
                candidate_zh=zh,
                channels=list(ranks),
                score=score,
                domain_tags=json.loads(row["domain_tags_json"]),
                locator=json.loads(row["locator_json"]),
            )
            ranked.append((sort_key, candidate))

    candidates = []
    seen_mirrors = set()
    for _, candidate in sorted(ranked, key=lambda entry: entry[0]):
        group = candidate["canonical_group"]
        term = normalize_text(candidate["candidate_zh"])
        if group and term:
            if (group, term) in seen_mirrors:
                continue
            seen_mirrors.add((group, term))
        candidates.append(candidate)
    return candidates, effective_mode


def _retrieve(
    db: sqlite3.Connection,
    item: dict[str, Any],
    domain_tag: str,
    target_region: str,
    target_locale: str,
    policy: dict[str, Any],
    top_k: int,
    mode: str,
) -> dict[str, Any]:
    if mode not in _MODES:
        raise ValueError(f"Unsupported retrieval mode: {mode!r}")
    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    query = {
        "en_text": item.get("en_text"),
        "zh_text": item.get("zh_text"),
        "hierarchy_path": item["hierarchy_path"],
        "domain_tag": domain_tag,
        "target_region": target_region,
        "target_locale": target_locale,
    }
    query.update({key: item[key] for key in ("control_type", "sibling_labels") if key in item})
    candidates, effective_mode = _rank_candidates(db, item, query, policy, mode)
    terms: dict[str, str] = {}
    for candidate in candidates:
        if supports_translation(candidate):
            terms.setdefault(normalize_text(candidate["candidate_zh"]), candidate["candidate_zh"])
    conflict = len(terms) > 1 and policy["conflict_rules"]["flag_same_scope_conflict"]
    return {
        "contract_version": CONTRACT_VERSION,
        "item_id": item["item_id"],
        "corpus_snapshot_hash": snapshot_hash(db),
        "retrieval_mode": effective_mode,
        "query": query,
        "candidates": candidates[:top_k],
        "conflict_flags": {
            "same_scope_conflict": conflict,
            "regional_variant_present": any(
                c["applicability"] == "regional_variant" for c in candidates
            ),
            "en_only_evidence": not terms
            and any(c["applicability"] == "en_definition_only" for c in candidates),
            "no_authoritative_hit": not terms,
            "conflicting_zh_terms": list(terms.values()) if conflict else [],
        },
    }


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
    """Retrieve controlled evidence for a UI item."""
    policy = load_authority_policy(policy_path, target_region)
    with read_index(db_path) as db:
        return _retrieve(db, item, domain_tag, target_region, target_locale, policy, top_k, mode)


def retrieve_for_page(
    db_path: Path,
    page_inventory_path: Path,
    target_region: str = "CN_MAINLAND",
    target_locale: str = "zh-CN",
    policy_path: Path | None = None,
    top_k: int = 8,
    mode: str = "glossary_fts",
) -> list[dict[str, Any]]:
    """Retrieve every item using the inventory's declared domain and one SQLite snapshot."""
    page = json.loads(page_inventory_path.read_text(encoding="utf-8"))
    policy = load_authority_policy(policy_path, target_region)
    with read_index(db_path) as db:
        return [
            _retrieve(
                db, item, page["domain_tag"], target_region, target_locale, policy, top_k, mode
            )
            for item in page["items"]
        ]
