"""Standalone integration tests backed by the frozen six-table index layout."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from lingual_audit.contracts import ContractValidationError, sha256_text, validate_payload
from lingual_audit.evaluate import predict_from_bundle, run_golden_evaluation
from lingual_audit.retrieval import retrieve_for_item, retrieve_for_page
from lingual_audit.review import (
    check_cross_menu_consistency,
    prepare_review_packets,
    validate_reviews_against_index,
)
from lingual_audit.state import atomic_write_json, default_authority_policy

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "golden" / "golden_set_v0.json"
FINANCE_POLICY = FIXTURES / "corpus" / "authority_policy.json"
SNAPSHOT = sha256_text("fixture corpus snapshot")
LOCATOR = {"format": "html", "file_sha256": sha256_text("fixture"), "heading_path": ["Terms"]}

DDL = """
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE documents(
    doc_id TEXT PRIMARY KEY, title TEXT, short_citation TEXT, publisher TEXT,
    doc_version TEXT, effective_date TEXT, format TEXT, file_sha256 TEXT,
    language_scope TEXT, target_locale TEXT, domain_tags_json TEXT, region_scope TEXT,
    normative_level TEXT, content_kind TEXT, canonical_group TEXT
);
CREATE TABLE glossary_entries(
    glossary_id TEXT PRIMARY KEY, doc_id TEXT, doc_version TEXT,
    en_term TEXT, en_normalized TEXT, en_aliases_json TEXT, zh_term TEXT, zh_normalized TEXT,
    target_locale TEXT, definition TEXT, field_code TEXT, domain_tags_json TEXT, region_scope TEXT,
    normative_level TEXT, support_type TEXT, locator_json TEXT, excerpt TEXT, excerpt_hash TEXT,
    pairing_verified INTEGER
);
CREATE TABLE document_chunks(
    chunk_id TEXT PRIMARY KEY, doc_id TEXT, doc_version TEXT,
    parent_clause TEXT, heading_path_json TEXT, text_verbatim TEXT, text_normalized TEXT,
    language TEXT, target_locale TEXT, domain_tags_json TEXT, region_scope TEXT,
    normative_level TEXT, support_type TEXT, locator_json TEXT, excerpt_hash TEXT
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_id UNINDEXED, doc_id UNINDEXED, text_normalized, cjk_bigrams
);
CREATE VIRTUAL TABLE glossary_fts USING fts5(
    glossary_id UNINDEXED, doc_id UNINDEXED, en_normalized, aliases_normalized,
    zh_bigrams, definition_normalized
);
"""


def bigrams(value: str) -> str:
    return " ".join(
        "".join(pair) for run in re.findall(r"[\u4e00-\u9fff]+", value) for pair in pairwise(run)
    )


def add_document(
    db: sqlite3.Connection,
    doc: str,
    domain: str,
    *,
    region: str = "CN_MAINLAND",
    locale: str | None = "zh-CN",
    tier: str = "domain_glossary",
    canonical_group: str | None = None,
    english_only: bool = False,
) -> None:
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc,
            f"Example source {doc}",
            doc,
            "Fixture Publisher",
            "2025",
            "2025-01-01",
            "html",
            LOCATOR["file_sha256"],
            "en_only" if english_only else "bilingual_en_zh",
            locale,
            json.dumps([domain]),
            region,
            tier,
            "bilingual_glossary",
            canonical_group,
        ),
    )


def add_term(
    db: sqlite3.Connection,
    key: str,
    doc: str,
    domain: str,
    en: str,
    zh: str | None,
    *,
    aliases: tuple[str, ...] = (),
    code: str | None = None,
    definition: str = "",
    verified: bool = True,
) -> None:
    excerpt = f"{en} | {zh}" if zh is not None else f"{en}: {definition}"
    en_normalized = en.lower()
    locale, region, tier = db.execute(
        "SELECT target_locale, region_scope, normative_level FROM documents WHERE doc_id = ?",
        (doc,),
    ).fetchone()
    db.execute(
        "INSERT INTO glossary_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            key,
            doc,
            "2025",
            en,
            en_normalized,
            json.dumps(aliases),
            zh,
            zh and zh.lower(),
            locale,
            definition,
            code,
            json.dumps([domain]),
            region,
            tier,
            "bilingual_equivalent" if zh else "concept_definition_only",
            json.dumps(LOCATOR),
            excerpt,
            sha256_text(excerpt),
            int(verified),
        ),
    )
    db.execute(
        "INSERT INTO glossary_fts VALUES (?,?,?,?,?,?)",
        (key, doc, en_normalized, " ".join(aliases).lower(), bigrams(zh or ""), definition.lower()),
    )


def add_chunk(db: sqlite3.Connection, doc: str, domain: str, excerpt: str) -> None:
    key = "CHK-DEF"
    db.execute(
        "INSERT INTO document_chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            key,
            doc,
            "2025",
            "1",
            '["Scope"]',
            excerpt,
            excerpt.lower(),
            "en",
            None,
            json.dumps([domain]),
            "GLOBAL",
            "international_standard",
            "concept_definition_only",
            json.dumps(LOCATOR),
            sha256_text(excerpt),
        ),
    )
    db.execute(
        "INSERT INTO chunks_fts VALUES (?,?,?,?)", (key, doc, excerpt.lower(), bigrams(excerpt))
    )


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    path = tmp_path / "index.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(DDL)
        db.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("contract_version", "1.0.0"),
                ("corpus_snapshot_hash", SNAPSHOT),
                ("snapshot_id", "SNAP-FIXTURE"),
                ("parser_version", "test"),
            ],
        )
    return path


@pytest.fixture
def finance_db(empty_db: Path) -> Path:
    with closing(sqlite3.connect(empty_db)) as db, db:
        add_document(db, "DOC-CN-FX-2025", "fx", tier="official_regulation", canonical_group="cn")
        add_document(db, "DOC-PDF-GLOSSARY", "fx", canonical_group="pdf")
        add_document(db, "DOC-MIRROR", "fx", canonical_group="cn")
        add_document(
            db,
            "DOC-HK-GLOSSARY-2025",
            "derivatives",
            region="HK",
            locale="zh-HK",
            tier="internal_confirmed",
        )
        add_document(db, "DOC-XLSX-FIELDS", "payments_messaging")
        add_document(
            db,
            "DOC-EN-RISK-ONLY",
            "risk_management",
            region="GLOBAL",
            locale=None,
            tier="international_standard",
            english_only=True,
        )
        for key, domain, en, zh in [
            ("COUNTERPARTY", "fx", "Counterparty", "交易对手方"),
            ("VALUE-DATE", "fx", "Value Date", "起息日"),
            ("MARGIN-FX", "fx", "Margin", "点差"),
            ("MARGIN-RISK", "risk_management", "Margin", "保证金"),
            ("FX", "fx", "FX", "外汇"),
            ("DERIVATIVES", "derivatives", "Derivatives", "衍生品"),
            ("NETTING", "risk_management", "Netting", "净额结算"),
            ("TENOR", "fx", "Tenor", "期限"),
            ("FOLDER", "fx", "Folder", "台账"),
        ]:
            add_term(db, f"GLS-{key}", "DOC-CN-FX-2025", domain, en, zh)
        add_term(
            db,
            "GLS-SETTLEMENT",
            "DOC-CN-FX-2025",
            "fx",
            "Settlement Instruction",
            "清算指令",
            code="fx.spot.settlement_instruction",
        )
        add_term(
            db,
            "GLS-IRS",
            "DOC-CN-FX-2025",
            "derivatives",
            "Interest Rate Swap",
            "利率互换",
            aliases=("ＩＲＳ",),
        )
        add_term(
            db,
            "GLS-BIC",
            "DOC-XLSX-FIELDS",
            "payments_messaging",
            "Business Identifier Code",
            "银行识别码",
            code="BIC",
        )
        add_term(
            db,
            "GLS-HK-DERIVATIVES",
            "DOC-HK-GLOSSARY-2025",
            "derivatives",
            "Derivatives",
            "衍生工具",
        )
        add_term(db, "GLS-ALT-FOLDER", "DOC-PDF-GLOSSARY", "fx", "Folder", "交易账簿")
        add_term(db, "GLS-MIRROR", "DOC-MIRROR", "fx", "Counterparty", "交易对手方 ")
        add_chunk(
            db, "DOC-EN-RISK-ONLY", "risk_management", "Synthetic Credit Overlay is defined here."
        )
    return empty_db


@pytest.fixture
def healthcare_db(empty_db: Path, tmp_path: Path) -> tuple[Path, Path]:
    with closing(sqlite3.connect(empty_db)) as db, db:
        add_document(db, "DOC-DEVICE-GLOSSARY", "device_labeling")
        add_document(db, "DOC-CLINICAL-STANDARD", "clinical", tier="industry_standard")
        add_term(
            db,
            "GLS-RECALL-MFG",
            "DOC-DEVICE-GLOSSARY",
            "device_labeling",
            "Recall",
            "召回",
            definition="Serial number quality check",
        )
        add_term(
            db,
            "GLS-RECALL-CLIN",
            "DOC-CLINICAL-STANDARD",
            "clinical",
            "Recall",
            "回访",
            definition="Follow-up schedule",
        )
        add_term(
            db,
            "GLS-BROKEN-PAIR",
            "DOC-DEVICE-GLOSSARY",
            "device_labeling",
            "Lead time",
            "导线时间",
            verified=False,
        )
    policy = default_authority_policy(policy_version="custom-1", industry_label="healthcare")
    policy["normative_precedence"] = ["domain_glossary", "industry_standard"]
    policy["domain_hierarchy"] = {
        "device_labeling": ["medical_products"],
        "medical_products": ["clinical"],
    }
    policy["conflict_rules"]["flag_same_scope_conflict"] = False
    path = tmp_path / "authority.json"
    atomic_write_json(path, policy)
    return empty_db, path


def ui(en: str | None, zh: str | None = None, **fields: Any) -> dict[str, Any]:
    return {
        "item_id": "UI-000001",
        "hierarchy_path": "Example > Labels",
        "control_type": "label",
        "en_text": en,
        "zh_text": zh,
        **fields,
    }


def evidence(db: Path, en: str | None, domain: str = "fx", **kwargs: Any) -> dict[str, Any]:
    result = retrieve_for_item(db, ui(en), domain, **kwargs)
    assert validate_payload("evidence", result) == []
    return result


def test_empty_index_and_hybrid_fallback(empty_db: Path) -> None:
    bundle = evidence(empty_db, "Unknown", mode="hybrid_vector_rerank")
    assert bundle["candidates"] == []
    assert bundle["retrieval_mode"] == "glossary_fts"
    assert bundle["conflict_flags"]["no_authoritative_hit"] is True
    assert predict_from_bundle(ui("Unknown", "a.b.c"), bundle)["verdict"] == "待核对"


@pytest.mark.parametrize(
    ("query", "domain", "zh", "channel"),
    [
        ("ＦＸ", "fx", "外汇", "exact_glossary"),
        ("IRS", "derivatives", "利率互换", "alias_glossary"),
        ("BIC", "payments_messaging", "银行识别码", "alias_glossary"),
        ("点差", "fx", "点差", "cjk_bigram"),
    ],
)
def test_short_queries(finance_db: Path, query: str, domain: str, zh: str, channel: str) -> None:
    first = evidence(finance_db, query, domain)["candidates"][0]
    assert first["candidate_zh"] == zh
    assert channel in first["channels"]


@pytest.mark.parametrize(
    "query",
    [
        "=SpotRate + Margin",
        '("' + "*:=+--)",
        "fx.spot.settlement_instruction",
    ],
)
def test_fts_operator_escaping(finance_db: Path, query: str) -> None:
    result = evidence(finance_db, query)
    if query.startswith("fx."):
        assert result["candidates"][0]["candidate_zh"] == "清算指令"


@pytest.mark.parametrize(
    ("domain", "zh", "other_zh"),
    [
        ("fx", "点差", "保证金"),
        ("risk_management", "保证金", "点差"),
    ],
)
def test_domain_precedes_wrong_ui_chinese(
    finance_db: Path,
    domain: str,
    zh: str,
    other_zh: str,
) -> None:
    bundle = retrieve_for_item(finance_db, ui("Margin", other_zh), domain)
    assert [(c["candidate_zh"], c["applicability"]) for c in bundle["candidates"]] == [
        (zh, "in_scope"),
        (other_zh, "cross_domain_reference"),
    ]
    assert bundle["conflict_flags"]["same_scope_conflict"] is False


def test_wrong_ui_chinese_and_siblings_do_not_supply_unrelated_translation(
    finance_db: Path,
) -> None:
    item = ui("Unindexed", "保证金", sibling_labels=["Margin", "Counterparty"])
    bundle = retrieve_for_item(finance_db, item, "risk_management")
    assert bundle["candidates"] == []
    assert predict_from_bundle(item, bundle)["verdict"] == "待核对"


def test_context_breaks_ties_within_same_scope_and_tier(empty_db: Path) -> None:
    with closing(sqlite3.connect(empty_db)) as db, db:
        add_document(db, "DOC-EQUIPMENT", "equipment")
        add_term(
            db,
            "GLS-A-CONTROL",
            "DOC-EQUIPMENT",
            "equipment",
            "Control",
            "控制项",
            definition="Instrument panel",
        )
        add_term(
            db,
            "GLS-B-CONTROL",
            "DOC-EQUIPMENT",
            "equipment",
            "Control",
            "对照组",
            definition="Study cohort",
        )
    for context, expected in (("Study cohort", "对照组"), ("Instrument panel", "控制项")):
        item = ui("Control", "待确认", sibling_labels=[context])
        bundle = retrieve_for_item(empty_db, item, "equipment")
        assert bundle["candidates"][0]["candidate_zh"] == expected
        assert bundle["conflict_flags"]["same_scope_conflict"] is True


def test_two_char_cjk_query_with_trigram_tokenizer(tmp_path: Path) -> None:
    path = tmp_path / "trigram.sqlite"
    ddl = DDL.replace(
        "zh_bigrams, definition_normalized", "zh_bigrams, definition_normalized, tokenize='trigram'"
    )
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(ddl)
        db.execute("INSERT INTO metadata VALUES (?,?)", ("corpus_snapshot_hash", SNAPSHOT))
        add_document(db, "DOC-DEVICE", "device_labeling")
        add_term(db, "GLS-RECALL", "DOC-DEVICE", "device_labeling", "Recall", "召回")
    bundle = retrieve_for_item(path, ui(None, "召回"), "device_labeling")
    assert validate_payload("evidence", bundle) == []
    assert bundle["candidates"][0]["channels"] == ["cjk_bigram"]


def test_regional_screening_precedes_authority(finance_db: Path) -> None:
    cn = evidence(finance_db, "Derivatives", "derivatives", policy_path=FINANCE_POLICY)
    hk = evidence(
        finance_db,
        "Derivatives",
        "derivatives",
        target_region="HK",
        target_locale="zh-HK",
        policy_path=FINANCE_POLICY,
    )
    for bundle, expected in ((cn, ["衍生品", "衍生工具"]), (hk, ["衍生工具", "衍生品"])):
        assert [c["candidate_zh"] for c in bundle["candidates"]] == expected
        assert [c["applicability"] for c in bundle["candidates"]] == [
            "in_scope",
            "regional_variant",
        ]
        assert bundle["conflict_flags"]["regional_variant_present"] is True


def test_conflict_survives_top_k_and_mirrors_do_not_inflate_support(finance_db: Path) -> None:
    conflict = evidence(finance_db, "Folder", top_k=1)
    assert len(conflict["candidates"]) == 1
    assert conflict["conflict_flags"]["same_scope_conflict"] is True
    assert set(conflict["conflict_flags"]["conflicting_zh_terms"]) == {"台账", "交易账簿"}
    assert predict_from_bundle(ui("Folder"), conflict)["verdict"] == "来源冲突"
    mirrors = evidence(finance_db, "Counterparty")
    assert [c["doc_id"] for c in mirrors["candidates"]] == ["DOC-CN-FX-2025"]


def test_vector_channel_fuses_with_lexical_evidence(finance_db: Path) -> None:
    with closing(sqlite3.connect(finance_db)) as db, db:
        db.execute(
            "CREATE TABLE glossary_embeddings(glossary_id TEXT PRIMARY KEY, embedding_json TEXT)"
        )
        db.executemany(
            "INSERT INTO glossary_embeddings VALUES (?,?)",
            [
                ("GLS-MARGIN-FX", "[1, 0]"),
                ("GLS-MARGIN-RISK", "[0, 1]"),
                ("GLS-FX", "malformed"),
            ],
        )
    bundle = retrieve_for_item(
        finance_db,
        ui("Margin", query_embedding=[1.0, 0.0]),
        "fx",
        mode="hybrid_vector",
    )
    assert validate_payload("evidence", bundle) == []
    assert bundle["retrieval_mode"] == "hybrid_vector"
    assert set(bundle["candidates"][0]["channels"]) == {
        "exact_glossary",
        "fts_bm25",
        "dense_vector",
    }
    assert bundle["candidates"][0]["score"] == pytest.approx(3 / 61)


def citation(bundle: dict[str, Any], index: int = 0) -> dict[str, str]:
    return {
        name: bundle["candidates"][index][name]
        for name in ("evidence_id", "doc_id", "doc_version", "excerpt_hash")
    }


def review_item(
    en: str, zh: str, *, domain: str = "fx", number: int = 1, **fields: Any
) -> dict[str, Any]:
    return {
        "item_id": f"UI-{number:06d}",
        "page_id": f"PG-{number:06d}",
        "menu_id": f"MN-{number:04d}",
        "hierarchy_path": "Example > Labels",
        "domain_tag": domain,
        "en_text": en,
        "zh_text": zh,
        "suggested_zh": None,
        "verdict": "保留",
        "business_rationale": "Observed terminology.",
        "evidence_refs": [],
        "human_review_status": "pending",
        **fields,
    }


def write_review(tmp_path: Path, *items: dict[str, Any], **fields: Any) -> Path:
    path = tmp_path / "review.json"
    atomic_write_json(
        path,
        {
            "contract_version": "1.0.0",
            "run_id": "RUN-20260101-0001",
            "policy_version": "1.0.0",
            "corpus_snapshot_hash": SNAPSHOT,
            "reviews": list(items),
            "consistency_findings": [],
            **fields,
        },
    )
    return path


def write_packets(tmp_path: Path, *bundles: dict[str, Any], name: str = "packets.json") -> Path:
    path = tmp_path / name
    atomic_write_json(
        path,
        {
            "contract_version": "1.0.0",
            "corpus_snapshot_hash": SNAPSHOT,
            "policy_version": "1.0.0",
            "items": [
                {
                    "ui_item": {"item_id": bundle["item_id"]},
                    "evidence_bundle": bundle,
                }
                for bundle in bundles
            ],
        },
    )
    return path


@pytest.fixture
def records(tmp_path: Path) -> Path:
    records_dir = tmp_path / "records"
    for number, domain, en, zh in (
        (2, "risk_management", "Margin", "保证金"),
        (1, "fx", "Counterparty", "反对党"),
    ):
        atomic_write_json(
            records_dir / "pages" / f"PG-{number:06d}.json",
            {
                "contract_version": "1.0.0",
                "artifact_kind": "page_inventory",
                "page_id": f"PG-{number:06d}",
                "menu_id": f"MN-{number:04d}",
                "observed_at": "2026-01-01T00:00:00Z",
                "domain_tag": domain,
                "menu_hierarchy_zh": ["示例"],
                "menu_hierarchy_en": ["Example"],
                "coverage_status": "fully_inspected",
                "items": [
                    ui(
                        en,
                        zh,
                        item_id=f"UI-{number:06d}",
                        control_id=f"CTL-{number:06d}",
                        state_id=f"ST-{number:06d}",
                        en_status="observed",
                        zh_status="observed",
                        text_kind="label",
                        pairing_method="stable_key_or_code",
                    )
                ],
            },
        )
    return records_dir


def test_review_packets_and_observed_text(finance_db: Path, records: Path, tmp_path: Path) -> None:
    output = tmp_path / "packets.json"
    summary = prepare_review_packets(finance_db, records, output, policy_path=FINANCE_POLICY)
    packets = json.loads(output.read_text(encoding="utf-8"))
    assert summary["page_count"] == summary["item_count"] == 2
    assert packets["corpus_snapshot_hash"] == SNAPSHOT
    assert packets["policy_version"] == summary["policy_version"] == "1.0.0"
    assert [row["page_id"] for row in packets["items"]] == ["PG-000001", "PG-000002"]
    page = records / "pages" / "PG-000001.json"
    bundle = packets["items"][0]["evidence_bundle"]
    assert retrieve_for_page(finance_db, page, policy_path=FINANCE_POLICY) == [bundle]
    assert (
        packets["items"][0]["ui_item"] == json.loads(page.read_text(encoding="utf-8"))["items"][0]
    )
    item = review_item(
        "Counterparty",
        "反对党",
        verdict="建议修改",
        suggested_zh="交易对手方",
        evidence_refs=[citation(bundle)],
    )
    assert validate_reviews_against_index(write_review(tmp_path, item), finance_db, records) == {
        "valid": True,
        "error_count": 0,
        "errors": [],
        "checked_reviews": 1,
        "checked_citations": 1,
    }
    item["en_text"] = "counterparty"
    item["zh_text"] = "反对党 "
    missing = review_item("Unobserved", "缺少", number=3)
    bad = validate_reviews_against_index(write_review(tmp_path, item, missing), finance_db, records)
    assert not bad["valid"]
    assert bad["error_count"] == 3


def test_english_only_chunk_cannot_authorize_change(finance_db: Path, tmp_path: Path) -> None:
    en = "Synthetic Credit Overlay"
    bundle = evidence(finance_db, en, "risk_management")
    assert bundle["conflict_flags"]["en_only_evidence"] is True
    assert bundle["conflict_flags"]["no_authoritative_hit"] is True
    assert bundle["candidates"][0]["channels"] == ["fts_bm25"]
    assert predict_from_bundle(ui(en, "旧译"), bundle)["verdict"] == "待核对"
    item = review_item(
        en, "旧译", verdict="建议修改", suggested_zh="新译", evidence_refs=[citation(bundle)]
    )
    result = validate_reviews_against_index(write_review(tmp_path, item), finance_db)
    assert result["checked_citations"] == 1
    assert not result["valid"]
    assert "EN-only" in result["errors"][0]
    item["evidence_refs"][0]["support_type"] = "bilingual_equivalent"
    assert not validate_reviews_against_index(write_review(tmp_path, item), finance_db)["valid"]


def test_change_verdict_requires_evidence(finance_db: Path, tmp_path: Path) -> None:
    item = review_item(
        "Counterparty",
        "反对党",
        verdict="建议修改",
        suggested_zh="交易对手方",
        evidence_refs=[],
    )

    result = validate_reviews_against_index(write_review(tmp_path, item), finance_db)

    assert result["valid"] is False
    assert any("evidence" in error.lower() for error in result["errors"])


@pytest.mark.parametrize("auto_discover", [False, True])
def test_citation_must_belong_to_items_prepared_bundle(
    finance_db: Path,
    records: Path,
    tmp_path: Path,
    auto_discover: bool,
) -> None:
    current_bundle = evidence(finance_db, "Counterparty")
    unrelated_bundle = evidence(finance_db, "Value Date")
    item = review_item(
        "Counterparty",
        "反对党",
        verdict="建议修改",
        suggested_zh="起息日",
        evidence_refs=[citation(unrelated_bundle)],
    )
    packets_path = write_packets(records if auto_discover else tmp_path, current_bundle)

    result = validate_reviews_against_index(
        write_review(tmp_path, item),
        finance_db,
        records_dir=records if auto_discover else None,
        packets_path=None if auto_discover else packets_path,
    )

    assert result["valid"] is False
    assert any("evidence bundle" in error for error in result["errors"])


def test_citation_in_items_prepared_bundle_is_valid(finance_db: Path, tmp_path: Path) -> None:
    bundle = evidence(finance_db, "Counterparty")
    item = review_item(
        "Counterparty",
        "反对党",
        verdict="建议修改",
        suggested_zh="交易对手方",
        evidence_refs=[citation(bundle)],
    )

    result = validate_reviews_against_index(
        write_review(tmp_path, item),
        finance_db,
        packets_path=write_packets(tmp_path, bundle),
    )

    assert result["valid"] is True


def test_empty_packet_bundle_allows_uncertain_review_without_citations(
    empty_db: Path, tmp_path: Path
) -> None:
    bundle = evidence(empty_db, "Unknown")
    item = review_item(
        "Unknown",
        "待确认",
        verdict="待核对",
        business_rationale="No authoritative evidence was retrieved.",
    )

    result = validate_reviews_against_index(
        write_review(tmp_path, item),
        empty_db,
        packets_path=write_packets(tmp_path, bundle),
    )

    assert result["valid"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("excerpt_hash", "0" * 64),
        ("evidence_id", "GLS-UNKNOWN"),
        ("doc_id", "DOC-OTHER"),
        ("doc_version", "2024"),
        ("corpus_snapshot_hash", "0" * 64),
    ],
)
def test_rejects_tampered_citations_and_snapshots(
    finance_db: Path,
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    ref = citation(evidence(finance_db, "Counterparty"))
    overrides = {}
    if field == "corpus_snapshot_hash":
        overrides[field] = value
    else:
        ref[field] = value
    item = review_item("Counterparty", "交易对手方", evidence_refs=[ref])
    result = validate_reviews_against_index(write_review(tmp_path, item, **overrides), finance_db)
    assert not result["valid"]
    assert result["checked_reviews"] == result["checked_citations"] == result["error_count"] == 1


def test_cross_menu_concept_drift_and_cross_domain_polysemy(tmp_path: Path) -> None:
    path = write_review(
        tmp_path,
        review_item("Margin", "点差"),
        review_item("ＭＡＲＧＩＮ", "保证金", domain="risk_management", number=2),
        review_item("Value Date", "起息日", concept_id="CON-DATE", number=3),
        review_item(
            "Date of Value",
            "起息日",
            concept_id="CON-DATE",
            number=4,
            verdict="建议修改",
            suggested_zh="交割日",
            evidence_refs=[
                {
                    "evidence_id": "GLS-VALUE-DATE",
                    "doc_id": "DOC-CN-FX-2025",
                    "doc_version": "2025",
                    "excerpt_hash": "d" * 64,
                }
            ],
        ),
        review_item("Margin", "忽略", number=5, verdict="待核对"),
    )
    result = check_cross_menu_consistency(path)
    assert (result["finding_count"], result["conflict_count"], result["advisory_count"]) == (
        2,
        1,
        1,
    )
    conflict, advisory = result["findings"]
    assert conflict["concept_key"] == "CON-DATE"
    assert conflict["distinct_zh_terms"] == ["起息日", "交割日"]
    assert advisory["item_ids"] == ["UI-000001", "UI-000002"]
    assert advisory["severity"] == "advisory_polysemy"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["consistency_findings"] = result["findings"]
    assert validate_payload("review", payload) == []


@pytest.mark.parametrize(("split", "count"), [("dev", 8), ("holdout", 6), ("all", 14)])
def test_golden_splits(finance_db: Path, split: str, count: int) -> None:
    report = run_golden_evaluation(GOLDEN, finance_db, split=split, policy_path=FINANCE_POLICY)
    assert validate_payload("eval_report", report) == []
    assert report["total_cases"] == len(report["case_results"]) == count
    assert report["metrics"]["evidence_recall_at_k"] == 1
    assert report["metrics"]["citation_integrity_rate"] == 1
    assert report["metrics"]["verdict_accuracy"] == 1
    assert report["metrics"]["abstention_precision"] == 1
    assert report["metrics"]["false_abstention_rate"] == 0
    if split != "dev":
        assert report["metrics"]["conflict_detection_rate"] == 1
    if split != "holdout":
        raw_key = next(row for row in report["case_results"] if row["case_id"] == "GS-0006")
        assert raw_key["predicted_verdict"] == "缺译"


@pytest.mark.parametrize("use_map", [False, True])
def test_healthcare_policy_and_external_predictions(
    healthcare_db: tuple[Path, Path],
    tmp_path: Path,
    use_map: bool,
) -> None:
    db, policy = healthcare_db
    item = ui("Recall", "回访", domain_tag="device_labeling", sibling_labels=["Serial number"])
    bundle = retrieve_for_item(db, item, item["domain_tag"], policy_path=policy)
    assert validate_payload("evidence", bundle) == []
    assert [(row["candidate_zh"], row["applicability"]) for row in bundle["candidates"]] == [
        ("召回", "in_scope"),
        ("回访", "in_scope"),
    ]
    assert bundle["conflict_flags"]["same_scope_conflict"] is False
    assert (
        evidence(db, "Lead time", "device_labeling")["conflict_flags"]["no_authoritative_hit"]
        is True
    )
    golden = tmp_path / "golden.json"
    atomic_write_json(
        golden,
        {
            "contract_version": "1.0.0",
            "artifact_kind": "golden_set",
            "dataset_version": "test-1",
            "cases": [
                {
                    "case_id": "GS-0001",
                    "split": "dev",
                    "concept_family": "device_recall",
                    "category": "standard_term",
                    "item": item,
                    "target_region": "CN_MAINLAND",
                    "target_locale": "zh-CN",
                    "expected_verdicts": ["建议修改"],
                    "acceptable_zh_terms": ["召回"],
                    "expected_doc_ids": ["DOC-DEVICE-GLOSSARY"],
                    "must_abstain": False,
                }
            ],
        },
    )
    baseline = run_golden_evaluation(golden, db, policy_path=policy, top_k=1)
    assert baseline["metrics"]["term_accuracy"] == 1
    assert baseline["metrics"]["verdict_accuracy"] == 1
    predictions = tmp_path / "predictions.json"
    prediction = {
        "verdict": "待核对",
        "evidence_refs": [citation(bundle), {**citation(bundle), "excerpt_hash": "0" * 64}],
    }
    atomic_write_json(
        predictions,
        {"GS-0001": prediction}
        if use_map
        else [
            {**prediction, "item_id": item["item_id"]},
        ],
    )
    external = run_golden_evaluation(golden, db, policy_path=policy, predictions_path=predictions)
    assert external["metrics"] == {
        "evidence_recall_at_k": 1,
        "citation_integrity_rate": 0.5,
        "verdict_accuracy": 0,
        "term_accuracy": 0,
        "conflict_detection_rate": 0,
        "abstention_precision": 0,
        "false_abstention_rate": 1,
    }
    assert external["per_category"]["standard_term"]["metrics"] == external["metrics"]


def test_invalid_policy_and_empty_eval_split(
    healthcare_db: tuple[Path, Path], tmp_path: Path
) -> None:
    db, _ = healthcare_db
    policy = tmp_path / "invalid-policy.json"
    atomic_write_json(policy, {})
    with pytest.raises(ContractValidationError):
        evidence(db, "Recall", "device_labeling", policy_path=policy)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    golden["cases"] = [case for case in golden["cases"] if case["split"] == "holdout"]
    path = tmp_path / "holdout-only.json"
    atomic_write_json(path, golden)
    with pytest.raises(ValueError, match="no cases"):
        run_golden_evaluation(path, db, split="dev")
