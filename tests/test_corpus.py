import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from enterprise_i18n_ops.contracts import sha256_text, validate_payload
from enterprise_i18n_ops.corpus import build_sqlite_index, ingest_corpus
from enterprise_i18n_ops.corpus.normalize import cjk_bigrams, normalize_text

FIXTURES = Path(__file__).parent / "fixtures" / "corpus"


def _records(directory, filename):
    return [
        json.loads(line) for line in (directory / filename).read_text(encoding="utf-8").splitlines()
    ]


def _document(doc_id, filename, **overrides):
    return {
        "doc_id": doc_id,
        "title": "Synthetic Assembly Reference",
        "publisher": "Synthetic Demonstration Publisher",
        "doc_version": "1",
        "format": Path(filename).suffix[1:],
        "rel_path": filename,
        "language_scope": "bilingual_en_zh",
        "target_locale": "zh-CN",
        "domain_tags": ["assembly"],
        "region_scope": "GLOBAL",
        "normative_level": "reference_material",
        "content_kind": "field_dictionary",
        "redistribution_status": "redistributable_fixture",
        **overrides,
    }


def _manifest(directory, *documents, snapshot_id="TEST-SNAPSHOT"):
    path = directory / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "artifact_kind": "corpus_manifest",
                "contract_version": "1.0.0",
                "snapshot_id": snapshot_id,
                "documents": documents,
            }
        ),
        encoding="utf-8",
    )
    return path


def _matches(db, table, query):
    assert table in {"glossary_fts", "chunks_fts"}
    return db.execute(f"SELECT doc_id FROM {table} WHERE {table} MATCH ?", (query,)).fetchall()


def _assert_record_contract(records, raw_field):
    for record in records:
        assert validate_payload(record["artifact_kind"], record) == []
        assert record["excerpt_hash"] == sha256_text(record[raw_field])


def test_search_normalization():
    assert normalize_text("  ＦＸ\n\t交易对⼿⽅  ") == "fx 交易对手方"
    assert cjk_bigrams("A⼿\n 外汇；交易对⼿⽅") == "手 外汇 交易 易对 对手 手方"


def test_fixture_ingest_and_fts_index(tmp_path):
    normalized = tmp_path / "normalized"
    summary = ingest_corpus(FIXTURES / "manifest.json", normalized)
    assert summary["errors"] == summary["warnings"] == []
    entries = _records(normalized, "glossary_entries.jsonl")
    chunks = _records(normalized, "document_chunks.jsonl")
    unverified = _records(normalized, "unverified_pairings.jsonl")
    _assert_record_contract(entries + unverified, "excerpt")
    _assert_record_contract(chunks, "text_verbatim")
    assert {rec["doc_id"] for rec in entries + chunks} == {
        "DOC-CN-FX-2025",
        "DOC-HK-GLOSSARY-2025",
        "DOC-EN-RISK-ONLY",
        "DOC-PDF-GLOSSARY",
        "DOC-XLSX-FIELDS",
    }

    radical_entry = next(e for e in entries if e["zh_term"] == "交易对⼿⽅")
    assert radical_entry["zh_normalized"] == "交易对手方"
    assert "交易对⼿⽅" in radical_entry["excerpt"]
    assert radical_entry["locator"]["physical_page"] == 1
    assert radical_entry["locator"]["printed_page"] == "1"
    assert len(radical_entry["locator"]["bbox"]) == 4
    assert any(c["doc_id"] == "DOC-PDF-GLOSSARY" for c in chunks)

    en_only = [rec for rec in entries + chunks if rec["doc_id"] == "DOC-EN-RISK-ONLY"]
    assert en_only
    assert all(rec["support_type"] == "concept_definition_only" for rec in en_only)
    assert all(rec.get("zh_term") is None for rec in en_only)
    assert all(rec["pairing_verified"] for rec in entries)
    assert {e["zh_term"] for e in unverified if e["en_term"] == "Margin"} == {"点差", "客户点差"}
    assert all(not rec["pairing_verified"] for rec in unverified)
    margin = next(c for c in chunks if c["locator"].get("anchor") == "clause-1-5")
    assert "“点差”或“客户点差”" in margin["text_verbatim"]
    assert margin["parent_clause"] == "第 1.5 条"

    bic = next(e for e in entries if e["en_term"] == "BIC")
    assert bic["locator"]["sheet_name"] == "FieldDictionary"
    assert bic["locator"]["cell_range"] == "A4:F4"
    assert bic["locator"]["row_index"] == 4
    assert bic["locator"]["field_code"] == "95P::BIC"

    db_path = tmp_path / "index.sqlite"
    build_sqlite_index(normalized, db_path)
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (5,)
        assert db.execute("SELECT COUNT(*) FROM glossary_entries").fetchone() == (len(entries),)
        for term in ("FX", "IRS", "BIC"):
            assert _matches(db, "glossary_fts", f"en_normalized:{term}")
        for term in ("FX", "IRS"):
            assert _matches(db, "chunks_fts", f"text_normalized:{term}")
        for tokens in ("起息 息日", "点差", "保证 证金", "外汇"):
            assert _matches(db, "glossary_fts", f'zh_bigrams:"{tokens}"')
            assert _matches(db, "chunks_fts", f'cjk_bigrams:"{tokens}"')
        assert _matches(db, "glossary_fts", "zh_bigrams:手")
        assert not _matches(db, "glossary_fts", "en_normalized:folder zh_bigrams:台账")


def test_empty_manifest_and_atomic_rebuild(tmp_path):
    manifest = _manifest(tmp_path)
    normalized = tmp_path / "normalized"
    first = ingest_corpus(manifest, normalized)
    second = ingest_corpus(_manifest(tmp_path, snapshot_id="OTHER"), normalized)
    assert first["corpus_snapshot_hash"] == second["corpus_snapshot_hash"]
    assert first["errors"] == second["errors"] == []
    resolved = json.loads((normalized / "manifest.resolved.json").read_text(encoding="utf-8"))
    assert resolved["documents"] == []
    assert resolved["corpus_snapshot_hash"] == first["corpus_snapshot_hash"]
    for name in ("glossary_entries", "document_chunks", "unverified_pairings"):
        assert (normalized / f"{name}.jsonl").read_bytes() == b""

    db_path = tmp_path / "db" / "index.sqlite"
    build_sqlite_index(normalized, db_path)
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
        assert metadata == {
            key: resolved[key]
            for key in ("contract_version", "snapshot_id", "corpus_snapshot_hash", "parser_version")
        }
        assert {
            name
            for _, name, kind, *_ in db.execute("PRAGMA table_list")
            if kind in {"table", "virtual"} and not name.startswith("sqlite_")
        } == {
            "metadata",
            "documents",
            "glossary_entries",
            "document_chunks",
            "chunks_fts",
            "glossary_fts",
        }
        for table in (
            "documents",
            "glossary_entries",
            "document_chunks",
            "chunks_fts",
            "glossary_fts",
        ):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)

    before = db_path.read_bytes()
    (normalized / "glossary_entries.jsonl").write_text("{broken json", encoding="utf-8")
    with pytest.raises(ValueError):
        build_sqlite_index(normalized, db_path)
    assert db_path.read_bytes() == before
    assert list(db_path.parent.iterdir()) == [db_path]


def test_industry_neutral_csv_and_html(tmp_path):
    (tmp_path / "parts.csv").write_text(
        "Part,Source,Target,Other Names,Domain,Definition,\n"
        'P-4," Jig  ","夹具",Fixture|Assembly Jig,assembly,Positions a workpiece.,\n',
        encoding="utf-8-sig",
    )
    (tmp_path / "procedure.html").write_text(
        "<nav>ignorednavigation</nav><script>ignoredscript</script>"
        "<style>.ignoredstyle {color: red}</style>"
        '<h1>Assembly</h1><section id="stations"><h2>Stations</h2>'
        '<p id="step">Use the <em>jig</em> to position the workpiece.</p>'
        "<dl><dt>治具</dt><dd>用来夹持工件。</dd></dl></section>",
        encoding="utf-8",
    )
    csv_doc = _document(
        "DOC-PARTS",
        "parts.csv",
        xlsx_columns={
            "field_code": "Part",
            "en_term": "Source",
            "zh_term": "Target",
            "aliases": "Other Names",
        },
    )
    html_doc = _document("DOC-PROCEDURE", "procedure.html", content_kind="reference_guide")
    normalized = tmp_path / "normalized"
    first = ingest_corpus(_manifest(tmp_path, csv_doc, html_doc), normalized)
    second = ingest_corpus(_manifest(tmp_path, html_doc, csv_doc), normalized)
    assert first["corpus_snapshot_hash"] == second["corpus_snapshot_hash"]
    assert first["errors"] == second["errors"] == []
    entries = _records(normalized, "glossary_entries.jsonl")
    chunks = _records(normalized, "document_chunks.jsonl")
    _assert_record_contract(entries, "excerpt")
    _assert_record_contract(chunks, "text_verbatim")
    (entry,) = entries
    assert entry["en_term"] == " Jig  "
    assert entry["en_normalized"] == "jig"
    assert entry["support_type"] == "bilingual_equivalent"
    assert entry["locator"] == {
        "format": "xlsx",
        "sheet_name": "CSV",
        "cell_range": "A2:F2",
        "row_index": 2,
        "field_code": "P-4",
        "file_sha256": first["documents"][0]["file_sha256"],
    }
    step = next(c for c in chunks if c["locator"].get("anchor") == "step")
    assert step["heading_path"] == ["Assembly", "Stations"]
    assert step["text_verbatim"] == "Use the jig to position the workpiece."
    assert any("用来夹持工件" in c["text_verbatim"] for c in chunks)
    assert all("ignored" not in c["text_verbatim"] for c in chunks)

    db_path = tmp_path / "index.sqlite"
    build_sqlite_index(normalized, db_path)
    with closing(sqlite3.connect(db_path)) as db:
        assert _matches(db, "glossary_fts", "zh_bigrams:夹具") == [("DOC-PARTS",)]
        assert _matches(db, "chunks_fts", "cjk_bigrams:工件") == [("DOC-PROCEDURE",)]


def test_manifest_hash_mismatch_preserves_output(tmp_path):
    (tmp_path / "parts.csv").write_text("EnglishTerm,ChineseTerm\nJig,夹具\n", encoding="utf-8")
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    previous = normalized / "manifest.resolved.json"
    previous.write_bytes(b"previous")
    manifest = _manifest(tmp_path, _document("DOC-PARTS", "parts.csv", file_sha256="0" * 64))
    with pytest.raises(ValueError, match="file_sha256.*DOC-PARTS"):
        ingest_corpus(manifest, normalized)
    assert previous.read_bytes() == b"previous"
    assert list(normalized.iterdir()) == [previous]


def _write_pdf(path, content):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(pdf)


def test_pdf_prose_and_blank_pdf_warning(tmp_path):
    _write_pdf(tmp_path / "blank.pdf", b"")
    _write_pdf(
        tmp_path / "guide.pdf",
        b"BT /F1 20 Tf 72 730 Td (Assembly Standard) Tj "
        b"/F1 11 Tf 0 -30 Td (Article 1.1: Secure the jig) Tj "
        b"0 -13 Td (before positioning the workpiece.) Tj "
        b"0 -25 Td (Article 1.2: Inspect the assembly.) Tj ET",
    )
    normalized = tmp_path / "normalized"
    manifest = _manifest(
        tmp_path,
        _document("DOC-BLANK", "blank.pdf"),
        _document(
            "DOC-GUIDE", "guide.pdf", content_kind="regulatory_standard", language_scope="en_only"
        ),
    )
    summary = ingest_corpus(manifest, normalized)
    assert summary["errors"] == []
    assert [(warning["doc_id"], warning["code"]) for warning in summary["warnings"]] == [
        ("DOC-BLANK", "pdf_no_extractable_text")
    ]
    chunks = _records(normalized, "document_chunks.jsonl")
    _assert_record_contract(chunks, "text_verbatim")
    assert {c["doc_id"] for c in chunks} == {"DOC-GUIDE"}
    clauses = {c["parent_clause"]: c for c in chunks}
    assert clauses["Article 1.1"]["text_verbatim"] == (
        "Article 1.1: Secure the jig\nbefore positioning the workpiece."
    )
    assert clauses["Article 1.1"]["heading_path"] == ["Assembly Standard"]
    assert clauses["Article 1.2"]["locator"]["clause"] == "Article 1.2"
    assert clauses["Article 1.2"]["locator"]["printed_page"] is None
