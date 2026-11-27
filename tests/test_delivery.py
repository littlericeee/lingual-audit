"""Deterministic workbook delivery tests using sanitized, contract-valid records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from openpyxl.packaging.relationship import Relationship
from openpyxl.styles import PatternFill
from openpyxl.workbook.external_link.external import ExternalBook, ExternalLink

from lingual_audit.contracts import (
    CONTRACT_VERSION,
    EXCEL_COLUMNS,
    VERDICT_STYLES,
    ContractValidationError,
    sha256_file,
    sha256_text,
    validate_payload,
)
from lingual_audit.delivery import export_workbook, verify_delivery

_EXPECTED_FILLS = {
    "保留": "E8F5E9",
    "建议修改": "FFF8E1",
    "缺译": "FFEBEE",
    "来源冲突": "FFF3E0",
    "单语限制": "ECEFF1",
    "待核对": "F3E5F5",
}
_FORMULA_LIKE_TEXT = ("=SpotRate + Margin", "+BP Spread", "@user", "-Delta", "\t=Note", "\r=Note")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _menu(index: int, label: str, *, group: bool = False) -> dict[str, Any]:
    return {
        "menu_id": f"MN-{index:04d}",
        "parent_menu_id": None,
        "is_group": group,
        "hierarchy_en": [f"Demo menu {index}"],
        "hierarchy_zh": [label],
        "domain_tag": "general_ui",
        "coverage_status": "registered_only",
    }


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    menu_path = tmp_path / "menu-map.json"
    review_path = tmp_path / "review.json"
    workbook_path = tmp_path / "delivery" / "workbook.xlsx"
    manifest_path = tmp_path / "delivery-manifest.json"
    _write_json(menu_path, {
        "contract_version": CONTRACT_VERSION,
        "artifact_kind": "menu_map",
        "observed_at": "2026-11-27T00:00:00Z",
        "menus": [
            _menu(1, "分组", group=True),
            {**_menu(2, "示例功能"), "parent_menu_id": "MN-0001"},
            _menu(3, "直接评审分组", group=True),
            _menu(4, "空功能"),
        ],
    })
    items = [
        {
            "item_id": f"UI-{index:06d}",
            "menu_id": "MN-0003" if verdict == "待核对" else "MN-0002",
            "page_id": "PG-000001",
            "hierarchy_path": f"示例功能 > 字段 {index}",
            "en_text": _FORMULA_LIKE_TEXT[index - 1] if index <= len(_FORMULA_LIKE_TEXT) else None,
            "zh_text": None if verdict == "缺译" else "示例字段",
            "suggested_zh": None if verdict == "保留" else "建议用语",
            "verdict": verdict,
            "business_rationale": "" if verdict == "保留" else "示例术语的业务依据",
            "evidence_refs": ([{
                "evidence_id": "GLS-DEMO-FIELD",
                "doc_id": "DOC-DEMO-GLOSSARY",
                "doc_version": "1.0",
                "excerpt_hash": "a" * 64,
            }] if verdict == "建议修改" else []),
            "human_review_status": "pending",
        }
        for index, verdict in enumerate(_EXPECTED_FILLS, start=1)
    ]
    _write_json(review_path, {
        "contract_version": CONTRACT_VERSION,
        "run_id": "RUN-20261127-0001",
        "corpus_snapshot_hash": "0" * 64,
        "policy_version": "1.0.0",
        "reviews": items,
        "consistency_findings": [{
            "concept_key": "demo_field",
            "domain_tag": "general_ui",
            "distinct_zh_terms": ["示例字段", "示例用语"],
            "item_ids": ["UI-000001", "UI-000002"],
            "severity": "conflict",
            "note": "=示例说明",
        }],
    })
    return menu_path, review_path, workbook_path, manifest_path


def test_exports_six_columns_and_all_verdicts(paths: tuple[Path, Path, Path, Path]) -> None:
    _, review_path, workbook_path, manifest_path = paths
    manifest = export_workbook(*paths)
    assert manifest == _read_json(manifest_path)
    assert validate_payload("delivery", manifest) == []
    assert manifest["readback_verified"]
    assert manifest["workbook_rel_path"] == "delivery/workbook.xlsx"
    assert manifest["workbook_sha256"] == sha256_file(workbook_path)
    assert [sheet["menu_id"] for sheet in manifest["sheets"]] == ["MN-0002", "MN-0003", "MN-0004"]
    assert [sheet["row_count"] for sheet in manifest["sheets"]] == [5, 1, 0]

    workbook = load_workbook(workbook_path, data_only=False)
    assert workbook.sheetnames == ["示例功能", "直接评审分组", "空功能"]
    for sheet in workbook.worksheets:
        assert tuple(cell.value for cell in sheet[1]) == EXCEL_COLUMNS
        assert sheet.max_column == 6
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == f"A1:F{sheet.max_row}"
        assert all(cell.font.bold and cell.fill.patternType == "solid" for cell in sheet[1])

    for mapping in manifest["row_mappings"]:
        row = workbook[mapping["sheet_name"]][mapping["row_number"]]
        verdict_cell = row[4]
        assert verdict_cell.value == mapping["verdict"]
        assert verdict_cell.fill.patternType == "solid"
        assert verdict_cell.fill.start_color.rgb[-6:] == _EXPECTED_FILLS[verdict_cell.value]
        assert verdict_cell.font.bold
        assert verdict_cell.font.color.rgb[-6:] == VERDICT_STYLES[verdict_cell.value]["font_hex"]
        assert all(cell.data_type == "s" and cell.number_format == "@" for cell in row)
        assert all(cell.alignment.wrap_text for cell in row)
        assert mapping["row_hash"] == sha256_text(
            json.dumps([cell.value for cell in row], ensure_ascii=False)
        )
    assert workbook["示例功能"]["D2"].value == ""
    assert workbook["示例功能"]["C4"].value == ""
    workbook.close()

    assert verify_delivery(workbook_path, manifest_path, review_path) == {
        "verified": True, "error_count": 0, "errors": [], "checked_sheets": 3, "checked_rows": 6
    }


def test_formula_like_ui_strings_remain_verbatim(paths: tuple[Path, Path, Path, Path]) -> None:
    _, review_path, workbook_path, manifest_path = paths
    export_workbook(*paths)
    workbook = load_workbook(workbook_path, data_only=False)
    for row_number, expected in enumerate(_FORMULA_LIKE_TEXT[:-1], start=2):
        cell = workbook["示例功能"].cell(row_number, 2)
        assert (cell.value, cell.data_type, cell.number_format) == (expected, "s", "@")
    cell = workbook["直接评审分组"].cell(2, 2)
    assert (cell.value, cell.data_type, cell.number_format) == (_FORMULA_LIKE_TEXT[-1], "s", "@")
    workbook.close()
    with ZipFile(workbook_path) as archive:
        assert b'<f' not in archive.read("xl/worksheets/sheet1.xml")
        assert not any(name.startswith("xl/externalLinks/") for name in archive.namelist())
    assert verify_delivery(workbook_path, manifest_path, review_path)["verified"]


def test_titles_and_optional_summary_sheets(paths: tuple[Path, Path, Path, Path]) -> None:
    menu_path, review_path, workbook_path, manifest_path = paths
    menu_map = _read_json(menu_path)
    long_title = "X" * 31
    menu_map["menus"].extend(
        _menu(index, label)
        for index, label in enumerate([
            "'FX/Spot: [Main]?*'", "Cash", "cash", long_title + "One", long_title + "Two",
            "_summary", "_CONSISTENCY", "cash_MN-0007",
        ], start=5)
    )
    _write_json(menu_path, menu_map)
    manifest = export_workbook(*paths, include_summary_sheets=True)
    workbook = load_workbook(workbook_path, data_only=False)
    names = [sheet["sheet_name"] for sheet in manifest["sheets"]]
    assert names[3:] == [
        "FX_Spot_ _Main___", "Cash", "cash_MN-0007", long_title,
        "X" * 23 + "_MN-0009", "_summary_MN-0010", "_CONSISTENCY_MN-0011",
        "cash_MN-0007_MN-0012",
    ]
    assert workbook.sheetnames == [*names, "_Summary", "_Consistency"]
    assert len({name.casefold() for name in workbook.sheetnames}) == len(workbook.sheetnames)
    assert all(0 < len(name) <= 31 for name in workbook.sheetnames)
    assert list(workbook["_Summary"].values)[1] == ("结论", "保留", "1")
    assert list(workbook["_Summary"].values)[7] == ("菜单", "示例功能", "5")
    finding = workbook["_Consistency"]
    assert list(finding.values)[1] == (
        "demo_field", "general_ui", '["示例字段", "示例用语"]',
        "UI-000001, UI-000002", "conflict", "=示例说明",
    )
    assert finding["F2"].data_type == "s"
    workbook.close()
    assert verify_delivery(workbook_path, manifest_path, review_path)["verified"]


@pytest.mark.parametrize(
    ("tamper", "error"),
    [
        ("value", "cell text differs from the review"),
        ("formula", "formula cell is forbidden"),
        ("summary_formula", "formula cell is forbidden"),
        ("fill", "verdict fill color mismatch"),
        ("order", "sheet names or order differ"),
        ("hyperlink", "external hyperlink is forbidden"),
        ("external_link", "Workbook contains external links"),
    ],
)
def test_detects_workbook_tampering(
    paths: tuple[Path, Path, Path, Path], tamper: str, error: str
) -> None:
    _, review_path, workbook_path, manifest_path = paths
    manifest = export_workbook(*paths, include_summary_sheets=True)
    workbook = load_workbook(workbook_path, data_only=False)
    if tamper == "value":
        workbook["示例功能"]["B2"] = "替换字段"
    elif tamper == "formula":
        workbook["示例功能"]["B2"] = "=SUM(1,2)"
    elif tamper == "summary_formula":
        workbook["_Summary"]["C2"] = "=SUM(1,2)"
    elif tamper == "fill":
        workbook["示例功能"]["E2"].fill = PatternFill("solid", fgColor="FFFFFF")
    elif tamper == "order":
        workbook.move_sheet("示例功能", offset=1)
    elif tamper == "hyperlink":
        workbook["_Summary"]["B2"].hyperlink = "https://example.invalid/demo"
    elif tamper == "external_link":
        link = ExternalLink(externalBook=ExternalBook(id="rId1"))
        link.file_link = Relationship(
            type="externalLinkPath", Target="file:///demo.xlsx", TargetMode="External"
        )
        workbook._external_links.append(link)
    workbook.save(workbook_path)
    workbook.close()

    report = verify_delivery(workbook_path, manifest_path, review_path)
    assert not report["verified"]
    assert report["error_count"] == len(report["errors"])
    assert any("workbook_sha256" in message for message in report["errors"])
    if tamper == "value":
        assert any("row_hash mismatch" in message for message in report["errors"])

    # Even a rewritten file checksum cannot hide a structural or safety failure.
    manifest["workbook_sha256"] = sha256_file(workbook_path)
    _write_json(manifest_path, manifest)
    report = verify_delivery(workbook_path, manifest_path, review_path)
    assert not report["verified"]
    assert any(error in message for message in report["errors"])


def test_detects_missing_review_mapping(paths: tuple[Path, Path, Path, Path]) -> None:
    _, review_path, workbook_path, manifest_path = paths
    manifest = export_workbook(*paths)
    missing_id = manifest["row_mappings"].pop()["item_id"]
    _write_json(manifest_path, manifest)

    report = verify_delivery(workbook_path, manifest_path, review_path)
    assert not report["verified"]
    assert f"Missing row mapping for review item {missing_id}" in report["errors"]


def test_rejects_unmapped_review_before_creating_outputs(
    paths: tuple[Path, Path, Path, Path]
) -> None:
    _, review_path, workbook_path, manifest_path = paths
    review = _read_json(review_path)
    review["reviews"][0]["menu_id"] = "MN-9999"
    _write_json(review_path, review)
    with pytest.raises(ContractValidationError, match="references unknown menu MN-9999"):
        export_workbook(*paths)
    assert not workbook_path.exists()
    assert not manifest_path.exists()
