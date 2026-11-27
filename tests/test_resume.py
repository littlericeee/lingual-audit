from __future__ import annotations

from pathlib import Path

import pytest

from lingual_audit.contracts import sha256_file
from lingual_audit.state import (
    atomic_write_text,
    init_workspace,
    load_run_state,
    resume_workspace,
    save_run_state,
)


def test_resume_requeues_running_units_without_replaying_inputs(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    state = init_workspace(ws, "RUN-20261127-7001", "Sanitized Demo", depth="thorough")
    state["task_units"].append(
        {
            "unit_id": "probe:PG-000001:en",
            "menu_id": "MN-0001",
            "page_id": "PG-000001",
            "state_id": "ST-000001",
            "language": "en",
            "status": "running",
            "blocker_reason": None,
            "updated_at": state["updated_at"],
        }
    )
    save_run_state(ws, state, "Interrupted during synthetic input probe.")

    resumed = resume_workspace(ws)

    assert resumed["requeued_units"] == ["probe:PG-000001:en"]
    unit = load_run_state(ws)["task_units"][0]
    assert unit["status"] == "pending"
    assert "clean page state" in unit["blocker_reason"]


def test_atomic_write_cleans_temp_file_after_replace_error(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "record.json"

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr("lingual_audit.state.os.replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        atomic_write_text(target, "payload")

    assert list(tmp_path.glob(".record.json.*.tmp")) == []
    assert not target.exists()


def test_resume_detects_corpus_manifest_snapshot_drift(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    state = init_workspace(ws, "RUN-20261127-7002", "Sanitized Demo")
    state["corpus_snapshot_hash"] = sha256_file(ws / "corpus" / "manifest.json")
    save_run_state(ws, state, "Pinned snapshots.")
    (ws / "corpus" / "manifest.json").write_text("{}\n", encoding="utf-8")

    resumed = resume_workspace(ws)

    assert resumed["safe_to_continue"] is False
    assert any("corpus manifest hash drift" in warning.lower() for warning in resumed["drift_warnings"])
