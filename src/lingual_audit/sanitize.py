"""Deterministic sanitization and release-safety scanner.

Security rule: Secret/credential paths are rejected strictly by path pattern
BEFORE opening or reading their contents.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "node_modules",
    }
)

FORBIDDEN_PATH_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "cookies.*",
    "*storage-state*.json",
    "*storage_state*.json",
    "auth.json",
    "*.auth.json",
    "*auth-state*.json",
    "*auth_state*.json",
    "id_rsa",
    "id_ed25519",
)

ALLOWED_ENV_TEMPLATES: frozenset[str] = frozenset({".env.example", ".env.test"})

FORBIDDEN_BROWSER_STATE_FILES: frozenset[str] = frozenset(
    {
        "cookies",
        "cookies-journal",
        "login data",
        "login data-journal",
        "web data",
        "web data-journal",
        "local state",
        "network persistent state",
        "storage-state.json",
    }
)

FORBIDDEN_DIR_SUBSTRINGS: tuple[str, ...] = ("profile", "cookies")

TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".html",
        ".htm",
        ".js",
        ".ts",
        ".css",
        ".csv",
        ".sh",
        ".ps1",
        ".ini",
        ".cfg",
    }
)

CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_ipv4",
        re.compile(
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
        ),
    ),
    (
        "windows_personal_path",
        re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+"),
    ),
    (
        "wsl_personal_path",
        re.compile(r"/mnt/[a-z]/Users/[^/\s\"']+"),
    ),
    (
        "unix_home_path",
        re.compile(r"(?:/home/[a-zA-Z0-9._-]+/|/Users/[a-zA-Z0-9._-]+/)"),
    ),
    (
        "private_key_header",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    (
        "openai_or_api_key",
        re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})\b"),
    ),
)


def is_forbidden_secret_path(rel_path: Path) -> bool:
    """Return True if path matches credential/secret patterns (checked without reading)."""
    name = rel_path.name.lower()
    if name in ALLOWED_ENV_TEMPLATES:
        return False
    if name in FORBIDDEN_BROWSER_STATE_FILES:
        return True
    for pat in FORBIDDEN_PATH_PATTERNS:
        if fnmatch.fnmatch(name, pat.lower()):
            return True
    for part in rel_path.parts[:-1]:
        lowered = part.lower()
        if (
            any(sub in lowered for sub in FORBIDDEN_DIR_SUBSTRINGS)
            and lowered not in SKIP_DIRS
            and (
                lowered.endswith("profile")
                or "browser-profile" in lowered
                or lowered == "cookies"
            )
        ):
            return True
    return False


def scan_directory(
    target_dir: Path,
    blocklist_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Scan a directory for forbidden secret paths and unsanitized content leaks."""
    root = target_dir.resolve()
    violations: list[dict[str, Any]] = []
    scanned_files = 0
    rejected_secret_paths = 0

    normalized_blocklist = [t.strip() for t in (blocklist_terms or []) if t.strip()]

    root_name = root.name.lower()
    if any(sub in root_name for sub in FORBIDDEN_DIR_SUBSTRINGS):
        rejected_secret_paths += 1
        violations.append(
            {
                "rule": "forbidden_secret_path",
                "path": ".",
                "line": None,
                "detail": "Credential or browser-profile root rejected without reading contents.",
            }
        )

    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.is_dir():
            continue

        if is_forbidden_secret_path(rel):
            rejected_secret_paths += 1
            violations.append(
                {
                    "rule": "forbidden_secret_path",
                    "path": rel.as_posix(),
                    "line": None,
                    "detail": "Credential or browser-state path rejected without reading contents.",
                }
            )
            continue

        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue

        # Avoid self-matching regex literals in this sanitizer file itself.
        if rel.as_posix() == "src/lingual_audit/sanitize.py":
            continue

        scanned_files += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            violations.append(
                {
                    "rule": "unreadable_file",
                    "path": rel.as_posix(),
                    "line": None,
                    "detail": str(exc),
                }
            )
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            for rule_name, rx in CONTENT_RULES:
                if rx.search(line):
                    violations.append(
                        {
                            "rule": rule_name,
                            "path": rel.as_posix(),
                            "line": lineno,
                            "detail": f"Matched sanitization rule '{rule_name}'.",
                        }
                    )
            lowered_line = line.lower()
            for term in normalized_blocklist:
                if term.lower() in lowered_line:
                    violations.append(
                        {
                            "rule": "blocklist_term",
                            "path": rel.as_posix(),
                            "line": lineno,
                            "detail": f"Matched forbidden blocklist term '{term}'.",
                        }
                    )

    return {
        "target_dir": root.as_posix(),
        "scanned_files": scanned_files,
        "rejected_secret_paths": rejected_secret_paths,
        "clean": len(violations) == 0,
        "violations": violations,
    }
