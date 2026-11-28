"""Environment capability probe for lingual-audit (read-only, never reads secrets)."""

from __future__ import annotations

import importlib
import importlib.metadata
import platform
import sqlite3
import sys
from pathlib import Path
from typing import Any

from lingual_audit.contracts import CONTRACT_VERSION, EXCEL_COLUMNS

_DIST_NAMES: dict[str, str] = {
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
}


def _probe_module(module_name: str) -> dict[str, Any]:
    try:
        mod = importlib.import_module(module_name)
        dist_name = _DIST_NAMES.get(module_name, module_name)
        try:
            version = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            version = getattr(mod, "__version__", "unknown")
        return {"available": True, "version": str(version)}
    except (ImportError, OSError, RuntimeError):
        return {"available": False, "version": None}


def _probe_sqlite_fts5() -> dict[str, Any]:
    con = sqlite3.connect(":memory:")
    try:
        options = [r[0] for r in con.execute("PRAGMA compile_options").fetchall()]
        has_fts5 = "ENABLE_FTS5" in options
        if has_fts5:
            con.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(content)")
        return {
            "sqlite_version": sqlite3.sqlite_version,
            "fts5_available": has_fts5,
        }
    finally:
        con.close()


def _probe_playwright_chromium(playwright_installed: bool) -> dict[str, Any]:
    if not playwright_installed:
        return {
            "playwright_installed": False,
            "chromium_ready": False,
            "chromium_executable": None,
            "error": "playwright package is not installed",
            "install_hint": (
                'python -m pip install -e ".[browser]" '
                "&& python -m playwright install chromium"
            ),
        }

    executable: str | None = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            executable = pw.chromium.executable_path
            if not executable or not Path(executable).is_file():
                return {
                    "playwright_installed": True,
                    "chromium_ready": False,
                    "chromium_executable": executable or None,
                    "error": "Chromium executable not found in Playwright browser cache",
                    "install_hint": "python -m playwright install chromium",
                }

            browser = None
            try:
                browser = pw.chromium.launch(headless=True)
            finally:
                if browser is not None:
                    browser.close()

        return {
            "playwright_installed": True,
            "chromium_ready": True,
            "chromium_executable": executable,
            "error": None,
            "install_hint": None,
        }
    except (Exception,) as exc:  # noqa: B013, BLE001 - doctor must never crash
        error_lines = str(exc).splitlines()
        return {
            "playwright_installed": True,
            "chromium_ready": False,
            "chromium_executable": executable or None,
            "error": error_lines[0] if error_lines else type(exc).__name__,
            "install_hint": "python -m playwright install chromium",
        }


def collect_doctor_report() -> dict[str, Any]:
    """Return a structured capability report for the current runtime environment."""
    core_modules = {
        "jsonschema": _probe_module("jsonschema"),
        "yaml": _probe_module("yaml"),
        "pdfplumber": _probe_module("pdfplumber"),
        "bs4": _probe_module("bs4"),
        "lxml": _probe_module("lxml"),
        "openpyxl": _probe_module("openpyxl"),
    }
    optional_modules = {
        "playwright": _probe_module("playwright"),
        "numpy": _probe_module("numpy"),
        "sentence_transformers": _probe_module("sentence_transformers"),
        "pytest": _probe_module("pytest"),
        "ruff": _probe_module("ruff"),
    }
    sqlite_info = _probe_sqlite_fts5()
    browser_info = _probe_playwright_chromium(
        optional_modules["playwright"]["available"]
    )

    ready_offline_core = (
        sqlite_info["fts5_available"]
        and core_modules["jsonschema"]["available"]
        and core_modules["pdfplumber"]["available"]
        and core_modules["openpyxl"]["available"]
    )

    return {
        "contract_version": CONTRACT_VERSION,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "sqlite": sqlite_info,
        "excel_columns": list(EXCEL_COLUMNS),
        "core_dependencies": core_modules,
        "optional_dependencies": optional_modules,
        "browser": browser_info,
        "capabilities": {
            "offline_core_ready": ready_offline_core,
            "browser_exploration_ready": browser_info["chromium_ready"],
            "semantic_vector_ready": (
                optional_modules["numpy"]["available"]
                and optional_modules["sentence_transformers"]["available"]
            ),
        },
    }
