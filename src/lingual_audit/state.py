"""Workspace state ledger, atomic checkpoint replacement, and Markdown handoff views."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lingual_audit.contracts import (
    CONTRACT_VERSION,
    ContractValidationError,
    sha256_file,
    validate_payload,
)


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically write UTF-8 text via a same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text)


def default_authority_policy(
    policy_version: str = "1.0.0",
    industry_label: str = "general",
    target_region: str = "CN_MAINLAND",
) -> dict[str, Any]:
    """Return a clean, industry-agnostic default authority policy template."""
    fallback = [target_region] if target_region == "GLOBAL" else [target_region, "GLOBAL"]
    return {
        "contract_version": CONTRACT_VERSION,
        "artifact_kind": "authority_policy",
        "policy_version": policy_version,
        "industry_label": industry_label,
        "normative_precedence": [
            "internal_confirmed",
            "official_regulation",
            "industry_standard",
            "international_standard",
            "domain_glossary",
            "reference_material",
        ],
        "region_fallback_order": {
            target_region: fallback,
            "GLOBAL": ["GLOBAL"],
        },
        "domain_hierarchy": {},
        "web_domain_tiers": {},
        "web_default_normative_level": "reference_material",
        "conflict_rules": {
            "preserve_regional_variants": True,
            "forbid_en_only_as_target_translation": True,
            "flag_same_scope_conflict": True,
        },
    }


def _render_task_plan(run_state: dict[str, Any]) -> str:
    phase = run_state["phase"]
    phases = [
        ("initialized", "Phase 1: Workspace initialized & scope pinned"),
        ("inventorying", "Phase 2: Bilingual UI discovery & control inventory"),
        ("inventory_frozen", "Phase 3: UI inventory frozen & coverage reconciled"),
        ("reviewing", "Phase 4: RAG retrieval & structured terminology review"),
        ("validated", "Phase 5: Citation integrity & cross-menu consistency validated"),
        ("exported", "Phase 6: 6-column color-coded Excel workbook exported"),
        ("verified", "Phase 7: Workbook read-back & final coverage verified"),
    ]
    order = [p for p, _ in phases]
    cur_idx = order.index(phase)
    lines = [
        f"# Task Plan ({run_state['run_id']})",
        "",
        f"- **App Label**: {run_state['scope']['app_label']}",
        f"- **Industry**: `{run_state['scope'].get('industry_label', 'general')}`",
        f"- **Depth**: `{run_state['depth']}`",
        f"- **Target Region / Locale**: `{run_state['target_region']}` / `{run_state['target_locale']}`",
        f"- **Policy Version**: `{run_state['policy_version']}`",
        f"- **Current Phase**: `{phase}`",
        "",
        "> Machine source of truth is `run.json` and `records/*.json`. This Markdown view is for human handoff only.",
        "",
        "## Phases",
        "",
    ]
    for idx, (_key, title) in enumerate(phases):
        mark = "x" if idx < cur_idx or (idx == cur_idx and phase == "verified") else " "
        status = "complete" if mark == "x" else ("in_progress" if idx == cur_idx else "pending")
        lines.append(f"- [{mark}] {title} (`{status}`)")
    return "\n".join(lines) + "\n"


def _render_progress(run_state: dict[str, Any], note: str) -> str:
    counts: dict[str, int] = {"pending": 0, "running": 0, "done": 0, "blocked": 0, "skipped": 0}
    for u in run_state.get("task_units", []):
        counts[u["status"]] = counts.get(u["status"], 0) + 1
    lines = [
        f"# Progress ({run_state['run_id']})",
        "",
        f"- Updated: `{run_state['updated_at']}`",
        f"- Phase: `{run_state['phase']}`",
        (
            f"- Task Units: done={counts['done']}, pending={counts['pending']}, "
            f"running={counts['running']}, blocked={counts['blocked']}, skipped={counts['skipped']}"
        ),
        "",
        "## Latest Checkpoint Note",
        "",
        f"- {note}",
    ]
    return "\n".join(lines) + "\n"


def init_workspace(
    workspace: Path,
    run_id: str,
    app_label: str,
    depth: str = "standard",
    target_region: str = "CN_MAINLAND",
    target_locale: str = "zh-CN",
    policy_version: str = "1.0.0",
    industry_label: str = "general",
    declared_menus: list[str] | None = None,
    role_alias: str = "read_only_auditor",
) -> dict[str, Any]:
    """Initialize an external audit workspace with empty BYOG corpus slot and default authority policy."""
    ws = workspace.resolve()
    for sub in ("records/pages", "corpus/raw", "corpus/normalized", "deliverables"):
        (ws / sub).mkdir(parents=True, exist_ok=True)

    now = utc_now_iso()
    run_state: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "created_at": now,
        "updated_at": now,
        "phase": "initialized",
        "depth": depth,
        "target_region": target_region,
        "target_locale": target_locale,
        "policy_version": policy_version,
        "corpus_snapshot_hash": None,
        "inventory_snapshot_hash": None,
        "scope": {
            "app_label": app_label,
            "industry_label": industry_label,
            "role_alias": role_alias,
            "declared_menus": declared_menus or ["ALL_VISIBLE_MENUS"],
            "allow_synthetic_input": depth == "thorough",
            "network_isolation_required": True,
        },
        "task_units": [],
    }

    errors = validate_payload("run", run_state)
    if errors:
        raise ContractValidationError(errors)

    empty_manifest: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "artifact_kind": "corpus_manifest",
        "snapshot_id": f"SNAP-{run_id}",
        "industry_label": industry_label,
        "created_at": now,
        "documents": [],
    }
    policy = default_authority_policy(
        policy_version=policy_version,
        industry_label=industry_label,
        target_region=target_region,
    )

    atomic_write_json(ws / "run.json", run_state)
    if not (ws / "corpus" / "manifest.json").exists():
        atomic_write_json(ws / "corpus" / "manifest.json", empty_manifest)
    if not (ws / "corpus" / "authority_policy.json").exists():
        atomic_write_json(ws / "corpus" / "authority_policy.json", policy)

    atomic_write_text(ws / "task_plan.md", _render_task_plan(run_state))
    atomic_write_text(
        ws / "findings.md",
        f"# Findings ({run_id})\n\n"
        f"- Workspace initialized for `{app_label}` (`industry={industry_label}`, `depth={depth}`).\n"
        "- Drop your domain PDF/HTML/XLSX/CSV glossaries or standards into `corpus/raw/` and declare them in `corpus/manifest.json`.\n",
    )
    atomic_write_text(
        ws / "progress.md",
        _render_progress(run_state, "Initialized empty BYOG corpus slot, default policy, and run checkpoints."),
    )
    return run_state


def load_run_state(workspace: Path) -> dict[str, Any]:
    run_path = workspace.resolve() / "run.json"
    if not run_path.is_file():
        raise FileNotFoundError(f"Workspace run.json not found: {run_path}")
    state = json.loads(run_path.read_text(encoding="utf-8"))
    errors = validate_payload("run", state)
    if errors:
        raise ContractValidationError(errors)
    return state


def save_run_state(workspace: Path, run_state: dict[str, Any], note: str) -> dict[str, Any]:
    run_state["updated_at"] = utc_now_iso()
    errors = validate_payload("run", run_state)
    if errors:
        raise ContractValidationError(errors)
    ws = workspace.resolve()
    atomic_write_json(ws / "run.json", run_state)
    atomic_write_text(ws / "task_plan.md", _render_task_plan(run_state))
    atomic_write_text(ws / "progress.md", _render_progress(run_state, note))
    return run_state


def compute_workspace_status(workspace: Path) -> dict[str, Any]:
    """Compute machine-authoritative coverage and checkpoint status from JSON records."""
    ws = workspace.resolve()
    run_state = load_run_state(ws)

    unit_counts: dict[str, int] = {"pending": 0, "running": 0, "done": 0, "blocked": 0, "skipped": 0}
    for u in run_state.get("task_units", []):
        unit_counts[u["status"]] = unit_counts.get(u["status"], 0) + 1

    menu_map_path = ws / "records" / "menu-map.json"
    page_files = (
        sorted((ws / "records" / "pages").glob("*.json"))
        if (ws / "records" / "pages").is_dir()
        else []
    )
    review_path = ws / "records" / "review.json"
    manifest_path = ws / "corpus" / "manifest.json"
    policy_path = ws / "corpus" / "authority_policy.json"
    index_path = ws / "corpus" / "index.sqlite"

    return {
        "workspace": ws.as_posix(),
        "run_id": run_state["run_id"],
        "phase": run_state["phase"],
        "depth": run_state["depth"],
        "industry_label": run_state["scope"].get("industry_label", "general"),
        "target_region": run_state["target_region"],
        "target_locale": run_state["target_locale"],
        "policy_version": run_state["policy_version"],
        "task_unit_counts": unit_counts,
        "artifacts": {
            "menu_map_present": menu_map_path.is_file(),
            "page_record_count": len(page_files),
            "review_present": review_path.is_file(),
            "corpus_manifest_present": manifest_path.is_file(),
            "authority_policy_present": policy_path.is_file(),
            "sqlite_index_present": index_path.is_file(),
        },
        "corpus_snapshot_hash": run_state.get("corpus_snapshot_hash"),
        "inventory_snapshot_hash": run_state.get("inventory_snapshot_hash"),
    }


def resume_workspace(workspace: Path) -> dict[str, Any]:
    """Safely recover a workspace after interruption without replaying interrupted input actions."""
    ws = workspace.resolve()
    run_state = load_run_state(ws)

    requeued_units: list[str] = []
    for unit in run_state.get("task_units", []):
        if unit["status"] == "running":
            unit["status"] = "pending"
            unit["blocker_reason"] = (
                "Re-queued after interrupted session; re-verify clean page state before resuming."
            )
            unit["updated_at"] = utc_now_iso()
            requeued_units.append(unit["unit_id"])

    drift_warnings: list[str] = []
    manifest_path = ws / "corpus" / "manifest.json"
    if manifest_path.is_file() and run_state.get("corpus_snapshot_hash"):
        actual_hash = sha256_file(manifest_path)
        if actual_hash != run_state["corpus_snapshot_hash"]:
            drift_warnings.append(
                f"Corpus manifest hash drift detected (recorded={run_state['corpus_snapshot_hash'][:12]}, "
                f"actual={actual_hash[:12]}). Downstream reviews must be re-validated."
            )

    if requeued_units:
        save_run_state(
            ws,
            run_state,
            f"Resumed workspace; re-queued {len(requeued_units)} interrupted unit(s) without replaying inputs.",
        )

    return {
        "run_id": run_state["run_id"],
        "phase": run_state["phase"],
        "requeued_units": requeued_units,
        "drift_warnings": drift_warnings,
        "safe_to_continue": len(drift_warnings) == 0,
    }
