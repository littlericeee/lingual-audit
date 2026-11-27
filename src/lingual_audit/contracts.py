"""Shared JSON Schema contracts, semantic validation, and repository constants."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

CONTRACT_VERSION = "1.0.0"

EXIT_CODES: dict[str, int] = {
    "success": 0,
    "invalid_arguments": 2,
    "contract_or_semantic_validation_failed": 10,
    "policy_or_authorization_blocked": 11,
    "integrity_or_citation_or_readback_failed": 12,
    "optional_capability_missing": 13,
    "sanitization_violation": 14,
}

EXCEL_COLUMNS: tuple[str, ...] = (
    "页面层级",
    "系统英文",
    "系统中文",
    "AI 建议中文",
    "比对结论",
    "修改理由与业务依据",
)

VERDICT_STYLES: dict[str, dict[str, str]] = {
    "保留": {"fill_hex": "E8F5E9", "font_hex": "1B5E20"},
    "建议修改": {"fill_hex": "FFF8E1", "font_hex": "8D6E00"},
    "缺译": {"fill_hex": "FFEBEE", "font_hex": "B71C1C"},
    "来源冲突": {"fill_hex": "FFF3E0", "font_hex": "E65100"},
    "单语限制": {"fill_hex": "ECEFF1", "font_hex": "37474F"},
    "待业务确认": {"fill_hex": "E3F2FD", "font_hex": "0D47A1"},
    "待核对": {"fill_hex": "F3E5F5", "font_hex": "4A148C"},
}

VALID_VERDICTS: tuple[str, ...] = tuple(VERDICT_STYLES.keys())

SCHEMA_MAP: dict[str, str] = {
    "common": "common.schema.json",
    "run": "run.schema.json",
    "ui_observation": "ui_observation.schema.json",
    "menu_map": "ui_observation.schema.json",
    "page_inventory": "ui_observation.schema.json",
    "corpus": "corpus.schema.json",
    "corpus_manifest": "corpus.schema.json",
    "authority_policy": "corpus.schema.json",
    "glossary_entry": "corpus.schema.json",
    "document_chunk": "corpus.schema.json",
    "evidence": "evidence.schema.json",
    "review": "review.schema.json",
    "delivery": "delivery.schema.json",
    "eval": "eval.schema.json",
    "golden_set": "eval.schema.json",
    "eval_report": "eval.schema.json",
}


class ContractValidationError(ValueError):
    """Raised when a JSON artifact fails schema or semantic invariant checks."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the repository root containing `contracts/`."""
    candidates = []
    if start is not None:
        candidates.append(start.resolve())
    candidates.append(Path(__file__).resolve().parent)
    candidates.append(Path.cwd().resolve())
    for base in candidates:
        for parent in (base, *base.parents):
            if (parent / "contracts" / "common.schema.json").is_file():
                return parent
    raise FileNotFoundError("Could not locate contracts/common.schema.json")


def contracts_dir(root: Path | None = None) -> Path:
    if root is not None:
        return root.resolve() / "contracts"
    try:
        return find_repo_root() / "contracts"
    except FileNotFoundError:
        return Path(__file__).resolve().parent / "schemas"


def load_cli_spec(root: Path | None = None) -> dict[str, Any]:
    path = contracts_dir(root) / "cli.json"
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_validator(kind: str, root: Path | None = None) -> Draft202012Validator:
    if kind not in SCHEMA_MAP:
        raise ValueError(f"Unsupported contract kind: {kind!r}")
    cdir = contracts_dir(root)
    schema_file = cdir / SCHEMA_MAP[kind]
    schema = json.loads(schema_file.read_text(encoding="utf-8"))

    # Support jsonschema >= 4.18 (referencing) and 4.10–4.17 (RefResolver) seamlessly.
    try:
        from referencing import Registry, Resource

        resources: list[tuple[str, Resource[Any]]] = []
        for sf in sorted(cdir.glob("*.schema.json")):
            doc = json.loads(sf.read_text(encoding="utf-8"))
            res = Resource.from_contents(doc)
            if "$id" in doc:
                resources.append((doc["$id"], res))
            resources.append((sf.name, res))
        registry = Registry().with_resources(resources)
        return Draft202012Validator(schema, registry=registry)
    except ImportError:
        store: dict[str, Any] = {}
        for sf in sorted(cdir.glob("*.schema.json")):
            doc = json.loads(sf.read_text(encoding="utf-8"))
            if "$id" in doc:
                store[doc["$id"]] = doc
            store[sf.name] = doc
            store[sf.resolve().as_uri()] = doc
        resolver = jsonschema.RefResolver(
            base_uri=schema.get("$id", schema_file.resolve().as_uri()),
            referrer=schema,
            store=store,
        )
        return Draft202012Validator(schema, resolver=resolver)


def _check_semantic_invariants(kind: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if kind == "menu_map" and payload.get("artifact_kind") != "menu_map":
        errors.append("Expected artifact_kind='menu_map'")
    elif kind == "page_inventory" and payload.get("artifact_kind") != "page_inventory":
        errors.append("Expected artifact_kind='page_inventory'")
    elif kind == "corpus_manifest" and payload.get("artifact_kind") != "corpus_manifest":
        errors.append("Expected artifact_kind='corpus_manifest'")
    elif kind == "authority_policy" and payload.get("artifact_kind") != "authority_policy":
        errors.append("Expected artifact_kind='authority_policy'")
    elif kind == "glossary_entry" and payload.get("artifact_kind") != "glossary_entry":
        errors.append("Expected artifact_kind='glossary_entry'")
    elif kind == "document_chunk" and payload.get("artifact_kind") != "document_chunk":
        errors.append("Expected artifact_kind='document_chunk'")
    elif kind == "golden_set" and payload.get("artifact_kind") != "golden_set":
        errors.append("Expected artifact_kind='golden_set'")
    elif kind == "eval_report" and payload.get("artifact_kind") != "eval_report":
        errors.append("Expected artifact_kind='eval_report'")

    artifact_kind = payload.get("artifact_kind")

    if kind in ("menu_map", "ui_observation") and artifact_kind == "menu_map":
        menus = payload.get("menus", [])
        seen_ids: set[str] = set()
        for m in menus:
            mid = m["menu_id"]
            if mid in seen_ids:
                errors.append(f"Duplicate menu_id: {mid}")
            seen_ids.add(mid)
        for m in menus:
            parent = m.get("parent_menu_id")
            if parent is not None and parent not in seen_ids:
                errors.append(f"Broken parent_menu_id {parent} referenced by {m['menu_id']}")

    if kind in ("page_inventory", "ui_observation") and artifact_kind == "page_inventory":
        state_ids = {s["state_id"] for s in payload.get("states", [])}
        seen_items: set[str] = set()
        for item in payload.get("items", []):
            iid = item["item_id"]
            if iid in seen_items:
                errors.append(f"Duplicate item_id: {iid}")
            seen_items.add(iid)
            if state_ids and item["state_id"] not in state_ids:
                errors.append(f"Broken state_id {item['state_id']} referenced by {iid}")
            if item["en_status"] == "observed" and not (item.get("en_text") or "").strip():
                errors.append(f"Item {iid} marked en_status=observed but en_text is empty")
            if item["zh_status"] == "observed" and not (item.get("zh_text") or "").strip():
                errors.append(f"Item {iid} marked zh_status=observed but zh_text is empty")

    if kind in ("corpus_manifest", "corpus") and artifact_kind == "corpus_manifest":
        seen_docs: set[str] = set()
        for doc in payload.get("documents", []):
            did = doc["doc_id"]
            if did in seen_docs:
                errors.append(f"Duplicate doc_id in corpus manifest: {did}")
            seen_docs.add(did)

    if kind == "review":
        seen_items = set()
        for rec in payload.get("reviews", []):
            iid = rec["item_id"]
            if iid in seen_items:
                errors.append(f"Duplicate review item_id: {iid}")
            seen_items.add(iid)
            verdict = rec["verdict"]
            rationale = (rec.get("business_rationale") or "").strip()
            if verdict != "保留" and len(rationale) < 4:
                errors.append(
                    f"Review {iid} has verdict={verdict!r} without substantive business_rationale"
                )
            if verdict == "建议修改" and not (rec.get("suggested_zh") or "").strip():
                errors.append(f"Review {iid} has verdict='建议修改' but suggested_zh is empty")
            if verdict == "建议修改" and not rec.get("evidence_refs"):
                errors.append(f"Review {iid} has verdict='建议修改' but evidence_refs is empty")

    if kind == "delivery":
        seen_sheets: set[str] = set()
        for sheet in payload.get("sheets", []):
            lowered = sheet["sheet_name"].lower()
            if lowered in seen_sheets:
                errors.append(f"Case-insensitive duplicate sheet_name: {sheet['sheet_name']}")
            seen_sheets.add(lowered)

    if kind in ("golden_set", "eval") and artifact_kind == "golden_set":
        seen_cases: set[str] = set()
        family_splits: dict[str, set[str]] = {}
        for case in payload.get("cases", []):
            cid = case["case_id"]
            if cid in seen_cases:
                errors.append(f"Duplicate golden case_id: {cid}")
            seen_cases.add(cid)
            family_splits.setdefault(case["concept_family"], set()).add(case["split"])
        for fam, splits in family_splits.items():
            if len(splits) > 1:
                errors.append(
                    f"Concept family {fam!r} leaks across dev and holdout splits"
                )

    return errors


def validate_payload(kind: str, payload: Any, root: Path | None = None) -> list[str]:
    """Validate a decoded JSON payload and return a list of validation errors (empty if valid)."""
    validator = _build_validator(kind, root=root)
    errors: list[str] = []
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.path)):
        loc = ".".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    if not errors and isinstance(payload, dict):
        errors.extend(_check_semantic_invariants(kind, payload))
    return errors


def validate_file(kind: str, path: Path, root: Path | None = None) -> list[str]:
    """Read and validate a JSON file against the given contract kind."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Failed to read JSON file {path}: {exc}"]
    return validate_payload(kind, payload, root=root)
