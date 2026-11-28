from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from lingual_audit.contracts import find_repo_root

REPO_ROOT = find_repo_root()
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def run(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def test_offline_synthetic_demo_pipeline(tmp_path: Path) -> None:
    cli = str(Path(sys.executable).with_name("lingual-audit"))

    normalized = tmp_path / "normalized"
    index = tmp_path / "index.sqlite"
    packets = tmp_path / "packets.json"
    review = tmp_path / "review.json"
    workbook = tmp_path / "audit.xlsx"
    manifest = tmp_path / "delivery-manifest.json"

    run(
        cli,
        "corpus",
        "ingest",
        "--manifest",
        str(FIXTURES / "corpus/manifest.json"),
        "--output-dir",
        str(normalized),
    )
    run(cli, "corpus", "index", "--normalized-dir", str(normalized), "--db-path", str(index))
    run(
        cli,
        "review",
        "prepare",
        "--db-path",
        str(index),
        "--records-dir",
        str(FIXTURES / "records"),
        "--output",
        str(packets),
        "--policy",
        str(FIXTURES / "corpus/authority_policy.json"),
    )
    run(sys.executable, str(REPO_ROOT / "examples/build_demo_review.py"), str(packets), str(review))
    run(
        cli,
        "review",
        "validate",
        "--review-file",
        str(review),
        "--db-path",
        str(index),
        "--records-dir",
        str(FIXTURES / "records"),
        "--packets",
        str(packets),
    )
    run(
        cli,
        "export",
        "--menu-map",
        str(FIXTURES / "records/menu-map.json"),
        "--review-file",
        str(review),
        "--output-xlsx",
        str(workbook),
        "--manifest-output",
        str(manifest),
        "--with-summary",
    )
    verified = run(
        cli,
        "verify",
        "--workbook",
        str(workbook),
        "--manifest",
        str(manifest),
        "--review-file",
        str(review),
    )

    assert workbook.is_file()
    assert json.loads(verified.stdout)["verified"] is True
    workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert isinstance(yaml.safe_load(workflow.read_text(encoding="utf-8")), dict)
