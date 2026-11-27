"""Environment capability probe for lingual-audit (read-only, never reads secrets)."""

from __future__ import annotations

import importlib
import importlib.metadata
import platform
import sqlite3
import sys
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
        "capabilities": {
            "offline_core_ready": ready_offline_core,
            "browser_exploration_ready": optional_modules["playwright"]["available"],
            "semantic_vector_ready": (
                optional_modules["numpy"]["available"]
                and optional_modules["sentence_transformers"]["available"]
            ),
        },
    }
