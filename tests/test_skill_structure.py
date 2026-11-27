from __future__ import annotations

import re

import yaml

from lingual_audit.contracts import (
    EXCEL_COLUMNS,
    VERDICT_STYLES,
    find_repo_root,
    validate_file,
)
from lingual_audit.sanitize import scan_directory

REPO_ROOT = find_repo_root()
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")


def test_skill_frontmatter() -> None:
    skill = REPO_ROOT / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["name"] == "lingual-audit"
    assert frontmatter["description"].strip()


def test_relative_markdown_links_resolve() -> None:
    files = [REPO_ROOT / "SKILL.md", *sorted((REPO_ROOT / "references").glob("*.md"))]
    for source in files:
        for target in MARKDOWN_LINK.findall(source.read_text(encoding="utf-8")):
            target = target.split("#", 1)[0].strip().strip("<>")
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            assert (source.parent / target).resolve().exists(), f"{source}: {target}"


def test_delivery_reference_lists_frozen_columns_and_verdicts() -> None:
    text = (REPO_ROOT / "references" / "rag-review-and-delivery.md").read_text(encoding="utf-8")
    assert all(column in text for column in EXCEL_COLUMNS)
    assert all(verdict in text for verdict in VERDICT_STYLES)


def test_example_templates_validate() -> None:
    templates = REPO_ROOT / "templates"
    assert validate_file("corpus_manifest", templates / "corpus_manifest.example.json") == []
    assert validate_file("authority_policy", templates / "authority_policy.example.json") == []


def test_no_vendored_companion_skills_and_repo_is_clean() -> None:
    assert not any(
        path.is_dir() and path.name in {"app-docs", "planning-with-files"}
        for path in REPO_ROOT.rglob("*")
    )
    assert scan_directory(REPO_ROOT)["clean"] is True
