"""Deterministic local recording of web-sourced terminology evidence."""

from __future__ import annotations

import csv
import html
import io
import json
import re
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lingual_audit.contracts import (
    ContractValidationError,
    contracts_dir,
    sha256_file,
    validate_payload,
)
from lingual_audit.state import atomic_write_json, atomic_write_text, utc_now_iso

_WEB_DOC_ID = re.compile(r"^DOC-WEB-(\d+)$")


def _web_host(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be an http:// or https:// URL with a hostname")
    return parsed.hostname.lower().rstrip(".")


@cache
def _normative_levels() -> frozenset[str]:
    common = json.loads((contracts_dir() / "common.schema.json").read_text(encoding="utf-8"))
    return frozenset(common["$defs"]["normative_level"]["enum"])


def _validated_normative_level(value: str) -> str:
    if value not in _normative_levels():
        raise ValueError(f"Invalid normative_level: {value!r}")
    return value


def resolve_web_normative_level(
    url: str,
    policy_path: Path | None = None,
    explicit_level: str | None = None,
) -> str:
    """Resolve a web source's authority tier using an explicit value or domain policy."""
    if explicit_level is not None:
        return _validated_normative_level(explicit_level)
    host = _web_host(url)
    if policy_path is None or not policy_path.exists():
        return "reference_material"

    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if errors := validate_payload("authority_policy", policy):
        raise ContractValidationError(errors)
    matching = []
    for configured_domain, level in policy.get("web_domain_tiers", {}).items():
        domain = configured_domain.lower().strip().strip(".")
        if domain and (host == domain or host.endswith(f".{domain}")):
            matching.append((len(domain), domain, level))
    if matching:
        return max(matching)[2]
    return policy.get("web_default_normative_level", "reference_material")


def _next_web_doc_id(documents: list[dict[str, Any]]) -> str:
    used = []
    for document in documents:
        if match := _WEB_DOC_ID.fullmatch(document.get("doc_id", "")):
            used.append(int(match.group(1)))
    return f"DOC-WEB-{max(used, default=0) + 1:06d}"


def _csv_evidence(en_term: str, zh_term: str, excerpt: str) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["en_term", "zh_term", "definition"])
    writer.writerow([en_term, zh_term, excerpt])
    return output.getvalue()


def add_web_evidence(
    manifest_path: Path,
    url: str,
    title: str,
    excerpt: str,
    en_term: str | None = None,
    zh_term: str | None = None,
    domain_tags: list[str] | None = None,
    region_scope: str = "GLOBAL",
    target_locale: str | None = None,
    policy_path: Path | None = None,
    normative_level: str | None = None,
    publisher: str | None = None,
    doc_version: str | None = None,
) -> dict[str, str]:
    """Append one locally cached web evidence document to a validated corpus manifest."""
    host = _web_host(url)
    if not title.strip():
        raise ValueError("title must be non-empty")
    if not excerpt.strip():
        raise ValueError("excerpt must be a non-empty verbatim excerpt")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if errors := validate_payload("corpus_manifest", manifest):
        raise ContractValidationError(errors)

    english = en_term.strip() if en_term and en_term.strip() else None
    chinese = zh_term.strip() if zh_term and zh_term.strip() else None
    resolved_level = resolve_web_normative_level(url, policy_path, normative_level)
    doc_id = _next_web_doc_id(manifest["documents"])
    is_bilingual = english is not None and chinese is not None
    file_format = "csv" if is_bilingual else "html"
    language_scope = "bilingual_en_zh" if is_bilingual else ("zh_only" if chinese else "en_only")
    filename = f"{doc_id}.{file_format}"
    artifact_path = manifest_path.parent / "web_cache" / filename
    if is_bilingual:
        content = _csv_evidence(english, chinese, excerpt)
    else:
        content = f"<h1>{html.escape(title)}</h1><p>{html.escape(excerpt)}</p>\n"
    atomic_write_text(artifact_path, content)

    document = {
        "doc_id": doc_id,
        "title": title,
        "short_citation": f"Web:{host}",
        "publisher": publisher or host,
        "doc_version": doc_version or datetime.now(UTC).date().isoformat(),
        "format": file_format,
        "rel_path": f"web_cache/{filename}",
        "file_sha256": sha256_file(artifact_path),
        "source_url": url,
        "fetched_at": utc_now_iso(),
        "language_scope": language_scope,
        "target_locale": target_locale if target_locale is not None else ("zh-CN" if chinese else None),
        "domain_tags": domain_tags if domain_tags is not None else ["general_ui"],
        "region_scope": region_scope,
        "normative_level": resolved_level,
        "content_kind": "bilingual_glossary" if is_bilingual else "reference_guide",
        "redistribution_status": "public_fetch_only",
    }
    manifest["documents"].append(document)
    if errors := validate_payload("corpus_manifest", manifest):
        raise ContractValidationError(errors)
    atomic_write_json(manifest_path, manifest)
    return {
        key: str(value)
        for key, value in {
            "doc_id": doc_id,
            "rel_path": document["rel_path"],
            "format": file_format,
            "normative_level": resolved_level,
            "language_scope": language_scope,
            "source_url": url,
            "file_sha256": document["file_sha256"],
            "manifest_path": manifest_path,
        }.items()
    }
