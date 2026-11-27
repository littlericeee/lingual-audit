"""Atomic artifact publication and reconstruction of the frozen SQLite index."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .normalize import cjk_search_tokens, normalize_text

_METADATA_KEYS = ("contract_version", "corpus_snapshot_hash", "snapshot_id", "parser_version")

_TABLE_COLUMNS = {
    "documents": (
        "doc_id",
        "title",
        "short_citation",
        "publisher",
        "doc_version",
        "effective_date",
        "format",
        "file_sha256",
        "language_scope",
        "target_locale",
        "domain_tags_json",
        "region_scope",
        "normative_level",
        "content_kind",
        "canonical_group",
    ),
    "glossary_entries": (
        "glossary_id",
        "doc_id",
        "doc_version",
        "en_term",
        "en_normalized",
        "en_aliases_json",
        "zh_term",
        "zh_normalized",
        "target_locale",
        "definition",
        "field_code",
        "domain_tags_json",
        "region_scope",
        "normative_level",
        "support_type",
        "locator_json",
        "excerpt",
        "excerpt_hash",
        "pairing_verified",
    ),
    "document_chunks": (
        "chunk_id",
        "doc_id",
        "doc_version",
        "parent_clause",
        "heading_path_json",
        "text_verbatim",
        "text_normalized",
        "language",
        "target_locale",
        "domain_tags_json",
        "region_scope",
        "normative_level",
        "support_type",
        "locator_json",
        "excerpt_hash",
    ),
}


@contextmanager
def staged_path(target: Path) -> Iterator[Path]:
    """Stage a file beside its destination for a same-filesystem replacement."""
    with NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as temp:
        path = Path(temp.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def json_line(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def _create_schema(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for table, columns in _TABLE_COLUMNS.items():
        declarations = [f"{columns[0]} TEXT PRIMARY KEY"]
        declarations.extend(
            f"{column} INTEGER" if column == "pairing_verified" else f"{column} TEXT"
            for column in columns[1:]
        )
        db.execute(f"CREATE TABLE {table}({', '.join(declarations)})")
    db.execute(
        "CREATE VIRTUAL TABLE chunks_fts USING fts5("
        "chunk_id UNINDEXED, doc_id UNINDEXED, text_normalized, cjk_bigrams)"
    )
    db.execute(
        "CREATE VIRTUAL TABLE glossary_fts USING fts5("
        "glossary_id UNINDEXED, doc_id UNINDEXED, en_normalized, aliases_normalized, "
        "zh_bigrams, definition_normalized)"
    )


def _column_value(record: dict[str, Any], column: str) -> Any:
    if column.endswith("_json"):
        value = record.get(column.removesuffix("_json"))
        return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else None
    if column == "pairing_verified":
        return int(record.get(column, True))
    return record.get(column)


def build_sqlite_index(normalized_dir: Path, db_path: Path) -> dict[str, Any]:
    """Rebuild SQLite glossary and FTS5 tables from the normalized artifacts."""
    manifest = json.loads((normalized_dir / "manifest.resolved.json").read_text(encoding="utf-8"))
    records = {
        "documents": manifest["documents"],
        "glossary_entries": _read_jsonl(normalized_dir / "glossary_entries.jsonl"),
        "document_chunks": _read_jsonl(normalized_dir / "document_chunks.jsonl"),
    }
    counts = dict.fromkeys(records, 0)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with staged_path(db_path) as temp_db:
        with closing(sqlite3.connect(temp_db)) as db, db:
            _create_schema(db)
            db.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                ((key, manifest[key]) for key in _METADATA_KEYS),
            )
            for table, rows in records.items():
                columns = _TABLE_COLUMNS[table]
                insert = f"INSERT INTO {table} VALUES ({', '.join('?' for _ in columns)})"
                for record in rows:
                    if table == "glossary_entries" and not record.get("pairing_verified", True):
                        continue
                    db.execute(insert, tuple(_column_value(record, col) for col in columns))
                    counts[table] += 1
                    if table == "glossary_entries":
                        db.execute(
                            "INSERT INTO glossary_fts VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                record["glossary_id"],
                                record["doc_id"],
                                record["en_normalized"],
                                normalize_text(" ".join(record.get("en_aliases", []))),
                                cjk_search_tokens(record.get("zh_normalized") or ""),
                                normalize_text(record.get("definition") or ""),
                            ),
                        )
                    elif table == "document_chunks":
                        normalized = record["text_normalized"]
                        db.execute(
                            "INSERT INTO chunks_fts VALUES (?, ?, ?, ?)",
                            (
                                record["chunk_id"],
                                record["doc_id"],
                                normalized,
                                cjk_search_tokens(normalized),
                            ),
                        )
        os.replace(temp_db, db_path)
    return {**{key: manifest[key] for key in _METADATA_KEYS}, **counts, "errors": []}
