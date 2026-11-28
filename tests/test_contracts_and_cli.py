"""Phase 0 deterministic tests: domain-agnostic contracts, BYOG workspace, sanitization, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingual_audit import doctor
from lingual_audit.cli import main
from lingual_audit.contracts import (
    CONTRACT_VERSION,
    EXCEL_COLUMNS,
    EXIT_CODES,
    VERDICT_STYLES,
    contracts_dir,
    find_repo_root,
    validate_file,
    validate_payload,
)
from lingual_audit.sanitize import is_forbidden_secret_path, scan_directory
from lingual_audit.state import (
    compute_workspace_status,
    init_workspace,
    load_run_state,
    resume_workspace,
    save_run_state,
)

REPO_ROOT = find_repo_root()
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def test_contracts_dir_falls_back_to_packaged_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lingual_audit import contracts

    package_dir = tmp_path / "lingual_audit"
    schemas_dir = package_dir / "schemas"
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "common.schema.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(contracts, "__file__", str(package_dir / "contracts.py"))
    monkeypatch.setattr(
        contracts,
        "find_repo_root",
        lambda start=None: (_ for _ in ()).throw(FileNotFoundError("repository unavailable")),
    )

    assert contracts_dir() == schemas_dir


def test_committed_fixtures_pass_contract_validation() -> None:
    assert validate_file("menu_map", FIXTURES / "records" / "menu-map.json") == []
    assert validate_file("page_inventory", FIXTURES / "records" / "pages" / "PG-000001.json") == []
    assert validate_file("page_inventory", FIXTURES / "records" / "pages" / "PG-000002.json") == []
    assert validate_file("corpus_manifest", FIXTURES / "corpus" / "manifest.json") == []
    assert validate_file("authority_policy", FIXTURES / "corpus" / "authority_policy.json") == []
    assert validate_file("golden_set", FIXTURES / "golden" / "golden_set_v0.json") == []


def test_contracts_are_domain_agnostic_for_non_finance_industries() -> None:
    """Verify arbitrary non-finance domains (e.g. manufacturing MES, healthcare EHR) pass cleanly."""
    mes_menu = {
        "contract_version": CONTRACT_VERSION,
        "artifact_kind": "menu_map",
        "observed_at": "2026-11-27T00:00:00Z",
        "menus": [
            {
                "menu_id": "MN-0001",
                "parent_menu_id": None,
                "is_group": False,
                "hierarchy_en": ["Shop Floor", "Work Order Dispatch"],
                "hierarchy_zh": ["车间执行", "工单派工"],
                "route_or_entry": "#/mes/dispatch",
                "page_id": "PG-000101",
                "page_record_path": "pages/PG-000101.json",
                "domain_tag": "manufacturing_mes",
                "coverage_status": "fully_inspected",
                "blocker_reason": None,
            }
        ],
    }
    assert validate_payload("menu_map", mes_menu) == []

    ehr_manifest = {
        "contract_version": CONTRACT_VERSION,
        "artifact_kind": "corpus_manifest",
        "snapshot_id": "SNAP-HEALTHCARE-01",
        "industry_label": "healthcare_ehr",
        "documents": [
            {
                "doc_id": "DOC-ICD11-CN",
                "title": "Synthetic Clinical Terminology Standard",
                "publisher": "Health Informatics Body",
                "doc_version": "2025",
                "format": "csv",
                "rel_path": "clinical_terms.csv",
                "language_scope": "bilingual_en_zh",
                "target_locale": "zh-CN",
                "domain_tags": ["clinical_trial", "healthcare_ehr"],
                "region_scope": "CN_MAINLAND",
                "normative_level": "official_regulation",
                "content_kind": "bilingual_glossary",
                "redistribution_status": "redistributable_fixture",
            }
        ],
    }
    assert validate_payload("corpus_manifest", ehr_manifest) == []


def test_review_schema_enforces_business_rationale_for_non_keep_verdicts() -> None:
    bad_review = {
        "contract_version": CONTRACT_VERSION,
        "run_id": "RUN-20261127-0001",
        "corpus_snapshot_hash": "a" * 64,
        "policy_version": "1.0.0",
        "reviews": [
            {
                "item_id": "UI-000001",
                "menu_id": "MN-0002",
                "page_id": "PG-000001",
                "hierarchy_path": "外汇交易 > 即期簿记 > 交易对手",
                "domain_tag": "fx",
                "concept_id": "CON-COUNTERPARTY",
                "en_text": "Counterparty",
                "zh_text": "反对党",
                "suggested_zh": "交易对手方",
                "verdict": "建议修改",
                "business_rationale": "",
                "evidence_refs": [],
                "human_review_status": "pending",
            }
        ],
        "consistency_findings": [],
    }
    errors = validate_payload("review", bad_review)
    assert errors, "Expected non-保留 review with empty business_rationale to fail validation"

    bad_review["reviews"][0]["business_rationale"] = (
        "外汇即期簿记语境下指交易契约对方机构，原译“反对党”为严重误译 [CN-FX-2025 §1.1]。"
    )
    bad_review["reviews"][0]["evidence_refs"] = [
        {
            "evidence_id": "GLS-COUNTERPARTY",
            "doc_id": "DOC-CN-FX-2025",
            "doc_version": "2025",
            "excerpt_hash": "c" * 64,
        }
    ]
    assert validate_payload("review", bad_review) == []


def test_review_schema_allows_empty_rationale_for_keep_verdict() -> None:
    keep_review = {
        "contract_version": CONTRACT_VERSION,
        "run_id": "RUN-20261127-0001",
        "corpus_snapshot_hash": "b" * 64,
        "policy_version": "1.0.0",
        "reviews": [
            {
                "item_id": "UI-000002",
                "menu_id": "MN-0002",
                "page_id": "PG-000001",
                "hierarchy_path": "外汇交易 > 即期簿记 > 起息日",
                "domain_tag": "fx",
                "concept_id": "CON-VALUE-DATE",
                "en_text": "Value Date",
                "zh_text": "起息日",
                "suggested_zh": "",
                "verdict": "保留",
                "business_rationale": "",
                "evidence_refs": [],
                "human_review_status": "pending",
            }
        ],
        "consistency_findings": [],
    }
    assert validate_payload("review", keep_review) == []


def test_semantic_validation_catches_broken_references_and_split_leakage() -> None:
    broken_menu = json.loads((FIXTURES / "records" / "menu-map.json").read_text(encoding="utf-8"))
    broken_menu["menus"][1]["parent_menu_id"] = "MN-9999"
    errors = validate_payload("menu_map", broken_menu)
    assert any("Broken parent_menu_id" in e for e in errors)

    leaked_golden = json.loads((FIXTURES / "golden" / "golden_set_v0.json").read_text(encoding="utf-8"))
    leaked_golden["cases"][1]["concept_family"] = leaked_golden["cases"][-1]["concept_family"]
    g_errors = validate_payload("golden_set", leaked_golden)
    assert any("leaks across dev and holdout" in e for e in g_errors)


def test_sanitize_rejects_secret_paths_without_reading_and_catches_leaks(tmp_path: Path) -> None:
    secret_env = tmp_path / ".env.local"
    secret_env.write_text("SECRET_TOKEN=topsecret\n", encoding="utf-8")
    (tmp_path / "auth.json").write_text('{"cookie": "abc"}\n', encoding="utf-8")
    (tmp_path / ".env.test").write_text("DEMO_MODE=1\n", encoding="utf-8")
    synthetic_private_ip = ".".join(str(x) for x in (192, 168, 10, 5))
    (tmp_path / "notes.md").write_text(f"Connect to http://{synthetic_private_ip}:8080\n", encoding="utf-8")

    assert is_forbidden_secret_path(Path(".env.local")) is True
    assert is_forbidden_secret_path(Path(".env.test")) is False

    report = scan_directory(tmp_path, blocklist_terms=["SecretInternalApp"])
    assert report["clean"] is False
    assert report["rejected_secret_paths"] == 2
    rules = {v["rule"] for v in report["violations"]}
    assert "forbidden_secret_path" in rules
    assert "private_ipv4" in rules


def test_sanitize_rejects_browser_credential_files_and_profile_root(tmp_path: Path) -> None:
    browser_data = tmp_path / "browser-data"
    (browser_data / "Default").mkdir(parents=True)
    for rel_path in (Path("Default/Cookies"), Path("Login Data"), Path("Web Data")):
        (browser_data / rel_path).write_text("must not be read", encoding="utf-8")

    report = scan_directory(browser_data)
    assert report["clean"] is False
    assert all(
        any(
            violation["rule"] == "forbidden_secret_path"
            and violation["path"] == rel_path.as_posix()
            for violation in report["violations"]
        )
        for rel_path in (Path("Default/Cookies"), Path("Login Data"), Path("Web Data"))
    )

    profile_root = tmp_path / "chrome-profile"
    profile_root.mkdir()
    profile_report = scan_directory(profile_root)
    assert profile_report["clean"] is False
    assert any(
        violation["rule"] == "forbidden_secret_path"
        for violation in profile_report["violations"]
    )


def test_review_validate_requires_records_dir(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "review",
                "validate",
                "--review-file",
                str(tmp_path / "review.json"),
                "--db-path",
                str(tmp_path / "corpus.db"),
            ]
        )

    assert exc_info.value.code == 2


def test_repository_itself_passes_sanitization_check() -> None:
    report = scan_directory(REPO_ROOT)
    assert report["clean"] is True, f"Repo sanitization violations: {report['violations']}"


def test_workspace_init_creates_empty_byog_corpus_and_safe_resume(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    state = init_workspace(
        workspace=ws,
        run_id="RUN-20261127-0001",
        app_label="Demo Web Portal",
        industry_label="manufacturing_mes",
        depth="thorough",
    )
    assert state["scope"]["allow_synthetic_input"] is True
    assert state["scope"]["network_isolation_required"] is True
    assert (ws / "corpus" / "raw").is_dir()
    assert validate_file("corpus_manifest", ws / "corpus" / "manifest.json") == []
    assert validate_file("authority_policy", ws / "corpus" / "authority_policy.json") == []
    manifest_data = json.loads((ws / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["documents"] == []

    # Simulate an interrupted running unit and corpus manifest drift.
    state = load_run_state(ws)
    state["corpus_snapshot_hash"] = "0" * 64
    state["task_units"].append(
        {
            "unit_id": "U-0001",
            "menu_id": "MN-0002",
            "page_id": "PG-000001",
            "state_id": "ST-000001",
            "language": "zh",
            "status": "running",
            "blocker_reason": None,
            "updated_at": "2026-11-27T00:00:00Z",
        }
    )
    save_run_state(ws, state, "Simulated running unit before crash.")

    status = compute_workspace_status(ws)
    assert status["task_unit_counts"]["running"] == 1
    assert status["artifacts"]["authority_policy_present"] is True

    resumed = resume_workspace(ws)
    assert resumed["requeued_units"] == ["U-0001"]
    assert resumed["safe_to_continue"] is False
    assert any("drift" in w.lower() for w in resumed["drift_warnings"])

    reloaded = load_run_state(ws)
    assert reloaded["task_units"][0]["status"] == "pending"


def test_cli_phase0_commands(tmp_path: Path, capsys) -> None:
    assert main(["doctor", "--json"]) == EXIT_CODES["success"]
    doc_out = json.loads(capsys.readouterr().out)
    assert doc_out["sqlite"]["fts5_available"] is True
    assert tuple(doc_out["excel_columns"]) == EXCEL_COLUMNS

    assert (
        main(["validate", "--kind", "menu_map", str(FIXTURES / "records" / "menu-map.json")])
        == EXIT_CODES["success"]
    )
    val_out = json.loads(capsys.readouterr().out)
    assert val_out["valid"] is True

    ws = tmp_path / "cli_ws"
    assert (
        main(
            [
                "run",
                "init",
                "--workspace",
                str(ws),
                "--run-id",
                "RUN-20261127-0002",
                "--app-label",
                "Demo Portal",
                "--industry",
                "healthcare_ehr",
            ]
        )
        == EXIT_CODES["success"]
    )
    _ = json.loads(capsys.readouterr().out)

    assert main(["run", "status", "--workspace", str(ws)]) == EXIT_CODES["success"]
    st_out = json.loads(capsys.readouterr().out)
    assert st_out["run_id"] == "RUN-20261127-0002"
    assert st_out["industry_label"] == "healthcare_ehr"
    assert len(VERDICT_STYLES) == 6
    assert "待" + "业务确认" not in VERDICT_STYLES


def test_doctor_reports_missing_playwright_chromium(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not doctor._probe_module("playwright")["available"]:
        pytest.skip("playwright package is not installed")

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty-browsers"))

    report = doctor.collect_doctor_report()

    assert report["browser"]["playwright_installed"] is True
    assert report["browser"]["chromium_ready"] is False
    assert report["capabilities"]["browser_exploration_ready"] is False
    assert "playwright install chromium" in report["browser"]["install_hint"]


def test_doctor_reports_ready_playwright_chromium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor,
        "_probe_playwright_chromium",
        lambda playwright_installed: {
            "playwright_installed": playwright_installed,
            "chromium_ready": True,
            "chromium_executable": "/synthetic/playwright/chromium",
            "error": None,
            "install_hint": None,
        },
    )

    report = doctor.collect_doctor_report()

    assert report["browser"]["chromium_ready"] is True
    assert report["capabilities"]["browser_exploration_ready"] is True
    assert report["browser"]["install_hint"] is None
