"""Corpus ingestion and rebuildable SQLite FTS5 indexing interface (Phase 1 Worker 1).

Domain-agnostic design: zero hardcoded industry keywords or domain lists.
Users supply any industry's PDF/HTML/XLSX/CSV glossaries and standards via
their workspace's corpus/manifest.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Frozen SQLite schema & public API contract shared between Worker 1 (corpus) and Worker 2 (retrieval/review):
# Tables created in index.sqlite by build_sqlite_index():
#   1. metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)
#      - keys: 'contract_version', 'corpus_snapshot_hash', 'snapshot_id', 'parser_version'
#   2. documents(doc_id TEXT PRIMARY KEY, title TEXT, short_citation TEXT, publisher TEXT,
#                doc_version TEXT, effective_date TEXT, format TEXT, file_sha256 TEXT,
#                language_scope TEXT, target_locale TEXT, domain_tags_json TEXT, region_scope TEXT,
#                normative_level TEXT, content_kind TEXT, canonical_group TEXT)
#   3. glossary_entries(glossary_id TEXT PRIMARY KEY, doc_id TEXT, doc_version TEXT,
#                       en_term TEXT, en_normalized TEXT, en_aliases_json TEXT,
#                       zh_term TEXT, zh_normalized TEXT, target_locale TEXT, definition TEXT,
#                       field_code TEXT, domain_tags_json TEXT, region_scope TEXT,
#                       normative_level TEXT, support_type TEXT, locator_json TEXT,
#                       excerpt TEXT, excerpt_hash TEXT, pairing_verified INTEGER)
#   4. document_chunks(chunk_id TEXT PRIMARY KEY, doc_id TEXT, doc_version TEXT,
#                      parent_clause TEXT, heading_path_json TEXT, text_verbatim TEXT,
#                      text_normalized TEXT, language TEXT, target_locale TEXT,
#                      domain_tags_json TEXT, region_scope TEXT, normative_level TEXT,
#                      support_type TEXT, locator_json TEXT, excerpt_hash TEXT)
#   5. chunks_fts (FTS5 virtual table):
#      - indexed columns: chunk_id UNINDEXED, doc_id UNINDEXED, text_normalized, cjk_bigrams
#   6. glossary_fts (FTS5 virtual table):
#      - indexed columns: glossary_id UNINDEXED, doc_id UNINDEXED, en_normalized, aliases_normalized, zh_bigrams, definition_normalized


def ingest_corpus(manifest_path: Path, output_dir: Path) -> dict[str, Any]:
    """Parse PDF/HTML/XLSX/CSV corpus files declared in manifest_path into normalized JSONL artifacts.

    Handles an empty documents list cleanly (BYOG workspace before user adds domain files).
    Must write:
      - output_dir / 'manifest.resolved.json' (with computed file_sha256 and corpus_snapshot_hash)
      - output_dir / 'glossary_entries.jsonl'
      - output_dir / 'document_chunks.jsonl'
      - output_dir / 'unverified_pairings.jsonl'
    """
    raise NotImplementedError("Phase 1 Worker 1: corpus.ingest_corpus not yet implemented")


def build_sqlite_index(normalized_dir: Path, db_path: Path) -> dict[str, Any]:
    """Rebuild SQLite glossary + FTS5 + CJK bigram index from normalized_dir into db_path."""
    raise NotImplementedError("Phase 1 Worker 1: corpus.build_sqlite_index not yet implemented")
