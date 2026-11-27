"""Export formula-safe Excel delivery workbooks and verify them against reviews."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from shutil import copyfileobj
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import LXML, Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.worksheet import Worksheet

from enterprise_i18n_ops.contracts import (
    CONTRACT_VERSION,
    EXCEL_COLUMNS,
    VERDICT_STYLES,
    ContractValidationError,
    sha256_file,
    sha256_text,
    validate_payload,
)
from enterprise_i18n_ops.state import atomic_write_json, utc_now_iso

_SUMMARY_SHEETS = ("_Summary", "_Consistency")
_ILLEGAL_TITLE = re.compile(r"[:\\/?*\[\]\x00-\x1f]")
_ROW_FIELDS = (
    "hierarchy_path", "en_text", "zh_text", "suggested_zh", "verdict", "business_rationale"
)
_WRAP = Alignment(vertical="top", wrap_text=True)


def _require_valid(kind: str, payload: Any) -> None:
    errors = validate_payload(kind, payload)
    if errors:
        raise ContractValidationError([f"{kind}: {error}" for error in errors])


def _load_payload(kind: str, path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require_valid(kind, payload)
    return payload


def _row_values(review: dict[str, Any]) -> list[str]:
    return [review[field] or "" for field in _ROW_FIELDS]


def _row_hash(values: Sequence[str]) -> str:
    return sha256_text(json.dumps(values, ensure_ascii=False))


def _sheet_name(label: str, menu_id: str, used_names: set[str]) -> str:
    base = _ILLEGAL_TITLE.sub("_", label).strip(" '\t\r\n") or menu_id
    name = base[:31].rstrip(" '")
    if name.casefold() in used_names:
        suffix = f"_{menu_id[-29:]}"
        name = base[:31 - len(suffix)] + suffix
        counter = 2
        while name.casefold() in used_names:
            numbered = f"{suffix[-(29 - len(str(counter))):]}_{counter}"
            name = base[:31 - len(numbered)] + numbered
            counter += 1
    used_names.add(name.casefold())
    return name


def _write_row(sheet: Worksheet, row_number: int, values: Sequence[str]) -> None:
    for column, value in enumerate(values, start=1):
        if len(value) > 32767:
            raise ValueError(f"{sheet.title}!{get_column_letter(column)}{row_number}: "
                             "text exceeds Excel's 32767-character cell limit")
        cell = sheet.cell(row_number, column, value)
        # Inline strings preserve formula-like labels without inserting an apostrophe.
        # An explicit empty text run survives openpyxl's empty-string write/read cycle.
        if value == "":
            cell.value = CellRichText([""])
        cell.data_type = "s"
        cell.number_format = "@"
        cell.alignment = _WRAP


def _format_sheet(
    sheet: Worksheet, headers: Sequence[str], widths: Sequence[int]
) -> None:
    _write_row(sheet, 1, headers)
    for column, width in enumerate(widths, start=1):
        cell = sheet.cell(1, column)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E7EBF0")
        sheet.column_dimensions[cell.column_letter].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _append_summaries(
    workbook: Workbook, sheets: list[dict[str, Any]], review: dict[str, Any]
) -> None:
    summary = workbook.create_sheet(_SUMMARY_SHEETS[0])
    counts = Counter(item["verdict"] for item in review["reviews"])
    summary_rows = [("结论", verdict, str(counts[verdict])) for verdict in VERDICT_STYLES]
    summary_rows.extend(("菜单", sheet["sheet_name"], str(sheet["row_count"])) for sheet in sheets)
    for row_number, values in enumerate(summary_rows, start=2):
        _write_row(summary, row_number, values)
    _format_sheet(summary, ("统计类型", "名称", "数量"), (16, 36, 12))

    consistency = workbook.create_sheet(_SUMMARY_SHEETS[1])
    for row_number, finding in enumerate(review["consistency_findings"], start=2):
        _write_row(consistency, row_number, (
            finding["concept_key"],
            finding["domain_tag"],
            json.dumps(finding["distinct_zh_terms"], ensure_ascii=False),
            ", ".join(finding["item_ids"]),
            finding["severity"],
            finding.get("note", ""),
        ))
    _format_sheet(
        consistency, ("概念", "领域", "中文术语", "条目 ID", "严重度", "备注"),
        (28, 20, 36, 30, 24, 50),
    )


def _save_workbook(workbook: Workbook, path: Path) -> None:
    workbook.save(path)
    if LXML:
        return

    # ElementTree emits raw CRs in text. XML parsers would normalize them to LF.
    with ZipFile(path) as original:
        names = {
            name for name in original.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            and b"\r" in original.read(name)
        }
        if not names:
            return

    with tempfile.TemporaryFile(dir=path.parent) as corrected:
        with ZipFile(path) as original, ZipFile(corrected, "w") as archive:
            for entry in original.infolist():
                data = original.read(entry)
                if entry.filename in names:
                    data = data.replace(b"\r", b"&#13;")
                archive.writestr(entry, data)
        corrected.seek(0)
        with path.open("wb") as destination:
            copyfileobj(corrected, destination)


@contextmanager
def _staged_path(directory: Path, suffix: str) -> Iterator[Path]:
    fd, filename = tempfile.mkstemp(dir=directory, suffix=suffix)
    path = Path(filename)
    try:
        os.close(fd)
        yield path
    finally:
        path.unlink(missing_ok=True)


def export_workbook(
    menu_map_path: Path,
    review_path: Path,
    output_xlsx: Path,
    manifest_output: Path,
    include_summary_sheets: bool = False,
) -> dict[str, Any]:
    """Export per-menu worksheets and publish the manifest after read-back passes."""
    menu_map = _load_payload("menu_map", menu_map_path)
    review = _load_payload("review", review_path)
    inputs = {menu_map_path.resolve(), review_path.resolve()}
    outputs = {output_xlsx.resolve(), manifest_output.resolve()}
    if len(outputs) != 2 or inputs & outputs:
        raise ValueError("Workbook and manifest must use distinct paths outside the inputs")

    menu_ids = {menu["menu_id"] for menu in menu_map["menus"]}
    by_menu: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in review["reviews"]:
        menu_id = item["menu_id"]
        if menu_id not in menu_ids:
            raise ContractValidationError([f"Review {item['item_id']} references unknown menu {menu_id}"])
        by_menu[menu_id].append(item)

    workbook = Workbook()
    workbook.remove(workbook.active)
    used_names = {name.casefold() for name in _SUMMARY_SHEETS} if include_summary_sheets else set()
    sheets: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    for menu in menu_map["menus"]:
        menu_id = menu["menu_id"]
        items = by_menu.get(menu_id, [])
        if menu["is_group"] and not items and not (menu.get("page_id") or menu.get("page_record_path")):
            continue
        hierarchy = menu["hierarchy_zh"]
        label = (hierarchy[-1].strip() if hierarchy else "") or menu["hierarchy_en"][-1]
        title = _sheet_name(label, menu_id, used_names)
        sheet = workbook.create_sheet(title)
        for row_number, item in enumerate(items, start=2):
            values = _row_values(item)
            _write_row(sheet, row_number, values)
            verdict = item["verdict"]
            style = VERDICT_STYLES[verdict]
            cell = sheet.cell(row_number, 5)
            cell.fill = PatternFill(
                fill_type="solid", start_color=style["fill_hex"], end_color=style["fill_hex"]
            )
            cell.font = Font(color=style["font_hex"], bold=True)
            mappings.append({
                "sheet_name": title, "row_number": row_number, "item_id": item["item_id"],
                "verdict": verdict, "row_hash": _row_hash(values),
            })
        _format_sheet(sheet, EXCEL_COLUMNS, (42, 34, 28, 28, 17, 58))
        sheets.append({
            "menu_id": menu_id, "sheet_name": title,
            "menu_hierarchy_zh": " > ".join(hierarchy), "row_count": len(items),
        })

    if include_summary_sheets:
        _append_summaries(workbook, sheets, review)
    if not workbook.worksheets:
        raise ValueError("Cannot export an Excel workbook with no deliverable sheets")

    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    workbook_parent = output_xlsx.parent.resolve()
    delivery_root = Path(os.path.commonpath([workbook_parent, manifest_output.parent.resolve()]))
    workbook_rel_path = (workbook_parent / output_xlsx.name).relative_to(delivery_root).as_posix()
    with (
        _staged_path(output_xlsx.parent, ".xlsx") as workbook_path,
        _staged_path(manifest_output.parent, ".json") as manifest_path,
    ):
        _save_workbook(workbook, workbook_path)
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "run_id": review["run_id"],
            "exported_at": utc_now_iso(),
            "workbook_rel_path": workbook_rel_path,
            "workbook_sha256": sha256_file(workbook_path),
            "columns": list(EXCEL_COLUMNS),
            "sheets": sheets,
            "row_mappings": mappings,
            "include_summary_sheets": include_summary_sheets,
            "readback_verified": False,
        }
        atomic_write_json(manifest_path, manifest)
        report = verify_delivery(workbook_path, manifest_path, review_path)
        manifest["readback_verified"] = report["verified"]
        if not manifest["readback_verified"]:
            raise ValueError("Workbook read-back failed: " + "; ".join(report["errors"]))
        _require_valid("delivery", manifest)

        atomic_write_json(manifest_path, manifest)
        os.replace(workbook_path, output_xlsx)
        os.replace(manifest_path, manifest_output)

    return manifest


def _check_workbook_safety(workbook: Workbook, errors: list[str]) -> None:
    if workbook._external_links:
        errors.append("Workbook contains external links")
    for sheet in workbook.worksheets:
        # Only inspect materialized cells so a far-away cell cannot inflate the scan to a full grid.
        for cell in sheet._cells.values():
            if cell.data_type == "f":
                errors.append(f"{sheet.title}!{cell.coordinate}: formula cell is forbidden")
            if cell.hyperlink is not None and cell.hyperlink.target:
                errors.append(f"{sheet.title}!{cell.coordinate}: external hyperlink is forbidden")


def _check_rows(
    workbook: Workbook, manifest: dict[str, Any], review: dict[str, Any], errors: list[str]
) -> tuple[int, int]:
    declarations = {sheet["sheet_name"]: sheet for sheet in manifest["sheets"]}
    expected_names = list(declarations)
    if manifest.get("include_summary_sheets", False):
        expected_names.extend(_SUMMARY_SHEETS)
    if workbook.sheetnames != expected_names:
        errors.append("Workbook sheet names or order differ from the manifest")
    if len({sheet["menu_id"] for sheet in declarations.values()}) != len(declarations):
        errors.append("Manifest declares multiple sheets for the same menu")
    if review["run_id"] != manifest["run_id"]:
        errors.append("Manifest run_id differs from the review run_id")

    worksheets = {sheet.title: sheet for sheet in workbook.worksheets}
    last_rows: dict[str, int] = {}
    for name, declaration in declarations.items():
        sheet = worksheets.get(name)
        if sheet is None:
            errors.append(f"Missing menu worksheet {name!r}")
            continue
        if sheet.max_column != len(EXCEL_COLUMNS) or tuple(
            sheet.cell(1, column).value for column in range(1, len(EXCEL_COLUMNS) + 1)
        ) != EXCEL_COLUMNS:
            errors.append(f"{name}: expected exact six-column headers")
        last_rows[name] = sheet.max_row
        if last_rows[name] != declaration["row_count"] + 1:
            errors.append(f"{name}: worksheet row count differs from the manifest")

    reviews = {item["item_id"]: item for item in review["reviews"]}
    seen_items: set[str] = set()
    seen_rows: set[tuple[str, int]] = set()
    checked_rows = 0
    for mapping in manifest["row_mappings"]:
        name, row_number, item_id = mapping["sheet_name"], mapping["row_number"], mapping["item_id"]
        location = f"{name}!{row_number} ({item_id})"
        coordinate = (name, row_number)
        if coordinate in seen_rows or item_id in seen_items:
            errors.append(f"{location}: duplicate row coordinate or review item mapping")
            continue
        seen_rows.add(coordinate)
        seen_items.add(item_id)

        declaration = declarations.get(name)
        sheet = worksheets.get(name)
        item = reviews.get(item_id)
        if item is None:
            errors.append(f"{location}: unknown review item")
        if declaration is None or sheet is None:
            errors.append(f"{location}: mapping references an undeclared or missing menu worksheet")
            continue
        if row_number > min(last_rows[name], declaration["row_count"] + 1):
            errors.append(f"{location}: mapped row is outside the declared worksheet data")
            continue

        checked_rows += 1
        cells = [sheet.cell(row_number, column) for column in range(1, len(EXCEL_COLUMNS) + 1)]
        values = []
        for cell in cells:
            if cell.number_format != "@" or not (
                (cell.data_type == "s" and isinstance(cell.value, str))
                or (cell.data_type == "inlineStr" and cell.value is None)
            ):
                errors.append(f"{name}!{cell.coordinate}: expected an explicit text cell")
            values.append("" if cell.value is None else cell.value)
        if all(isinstance(value, str) for value in values) and (
            _row_hash(values) != mapping["row_hash"]
        ):
            errors.append(f"{location}: row_hash mismatch")
        if item is not None:
            if declaration["menu_id"] != item["menu_id"]:
                errors.append(f"{location}: mapped to the wrong review menu")
            if mapping["verdict"] != item["verdict"]:
                errors.append(f"{location}: manifest verdict differs from the review")
            if values != _row_values(item):
                errors.append(f"{location}: cell text differs from the review")

        verdict = item["verdict"] if item is not None else mapping["verdict"]
        fill = cells[4].fill
        if (fill.patternType != "solid" or fill.start_color.type != "rgb"
                or not fill.start_color.rgb.upper().endswith(VERDICT_STYLES[verdict]["fill_hex"])):
            errors.append(f"{location}: verdict fill color mismatch")

    for item_id in sorted(reviews.keys() - seen_items):
        errors.append(f"Missing row mapping for review item {item_id}")
    coordinates_by_sheet = Counter(name for name, _ in seen_rows)
    for name, declaration in declarations.items():
        if coordinates_by_sheet[name] != declaration["row_count"]:
            errors.append(f"{name}: row mapping count differs from the declared row count")
    return len(last_rows), checked_rows


def verify_delivery(
    workbook_path: Path,
    manifest_path: Path,
    review_path: Path,
) -> dict[str, Any]:
    """Verify the file hash, review coverage, workbook structure, and literal cell contents."""
    errors: list[str] = []
    checked_sheets = checked_rows = 0
    try:
        manifest = _load_payload("delivery", manifest_path)
        review = _load_payload("review", review_path)
        if sha256_file(workbook_path) != manifest["workbook_sha256"]:
            errors.append("Workbook SHA-256 mismatch (workbook_sha256)")
        workbook = load_workbook(workbook_path, data_only=False, keep_links=True)
        try:
            _check_workbook_safety(workbook, errors)
            checked_sheets, checked_rows = _check_rows(workbook, manifest, review, errors)
        finally:
            workbook.close()
    except ContractValidationError as exc:
        errors.extend(exc.errors)
    except (OSError, ValueError, KeyError, BadZipFile, SyntaxError, InvalidFileException) as exc:
        errors.append(f"Could not read delivery artifacts: {exc}")

    return {
        "verified": not errors,
        "error_count": len(errors),
        "errors": errors,
        "checked_sheets": checked_sheets,
        "checked_rows": checked_rows,
    }
