"""Corpus ingestion and rebuildable SQLite FTS5 indexing interface (Phase 1 Worker 1).

Domain-agnostic design: zero hardcoded industry keywords or domain lists.
Users supply any industry's PDF/HTML/XLSX/CSV glossaries and standards via
their workspace's corpus/manifest.json.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from lingual_audit.contracts import (
    ContractValidationError,
    sha256_file,
    sha256_text,
    validate_payload,
)
from lingual_audit.sanitize import is_forbidden_secret_path

from .parsers import parse_document
from .storage import build_sqlite_index as build_sqlite_index
from .storage import json_line, staged_path

PARSER_VERSION = "1.0.0"

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
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if errors := validate_payload("corpus_manifest", manifest):
        raise ContractValidationError(errors)

    root = manifest_path.parent.resolve()
    documents = []
    sources = []
    for doc in sorted(manifest["documents"], key=lambda item: item["doc_id"]):
        relative = Path(doc["rel_path"])
        path = (root / relative).resolve()
        if (
            not path.is_relative_to(root)
            or is_forbidden_secret_path(relative)
            or is_forbidden_secret_path(path.relative_to(root))
        ):
            raise ValueError(f"Unsafe corpus path for {doc['doc_id']}")
        actual_hash = sha256_file(path)
        if (expected_hash := doc.get("file_sha256")) and expected_hash != actual_hash:
            raise ValueError(f"file_sha256 mismatch for {doc['doc_id']}")
        documents.append({**doc, "file_sha256": actual_hash})
        sources.append(path)

    identities = [(doc["doc_id"], doc["doc_version"], doc["file_sha256"]) for doc in documents]
    snapshot_hash = sha256_text(json.dumps([PARSER_VERSION, identities], ensure_ascii=False))
    resolved = {
        **manifest,
        "documents": documents,
        "parser_version": PARSER_VERSION,
        "corpus_snapshot_hash": snapshot_hash,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    counts = dict.fromkeys(("glossary_entries", "document_chunks", "unverified_pairings"), 0)
    warnings = []
    with ExitStack() as staging:
        targets = {kind: output_dir / f"{kind}.jsonl" for kind in counts}
        targets["manifest"] = output_dir / "manifest.resolved.json"
        paths = {
            kind: staging.enter_context(staged_path(target)) for kind, target in targets.items()
        }
        with ExitStack() as streams:
            outputs = {
                kind: streams.enter_context(paths[kind].open("w", encoding="utf-8", newline="\n"))
                for kind in counts
            }
            for path, doc in zip(sources, documents, strict=True):
                parsed = parse_document(path, doc, manifest["contract_version"])
                warnings.extend(parsed.warnings)
                for entry in parsed.entries:
                    kind = (
                        "glossary_entries" if entry["pairing_verified"] else "unverified_pairings"
                    )
                    outputs[kind].write(json_line(entry))
                    counts[kind] += 1
                for chunk in parsed.chunks:
                    outputs["document_chunks"].write(json_line(chunk))
                    counts["document_chunks"] += 1
        paths["manifest"].write_text(json_line(resolved), encoding="utf-8", newline="\n")
        for kind, target in targets.items():
            os.replace(paths[kind], target)

    return {**resolved, **counts, "warnings": warnings, "errors": []}
