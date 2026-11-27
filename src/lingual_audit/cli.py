"""Unified CLI entrypoint for lingual-audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lingual_audit.contracts import (
    EXIT_CODES,
    SCHEMA_MAP,
    ContractValidationError,
    validate_file,
)
from lingual_audit.doctor import collect_doctor_report
from lingual_audit.sanitize import scan_directory
from lingual_audit.state import (
    compute_workspace_status,
    init_workspace,
    resume_workspace,
)


def _emit_json(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _emit_command_error(exc: Exception) -> int:
    """Map expected command failures to the frozen CLI exit-code contract."""
    sys.stderr.write(f"Error: {exc}\n")
    if isinstance(exc, FileNotFoundError):
        return EXIT_CODES["integrity_or_citation_or_readback_failed"]
    return EXIT_CODES["contract_or_semantic_validation_failed"]


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = collect_doctor_report()
    if args.json:
        _emit_json(report)
    else:
        print(f"Python: {report['python_version']}")
        print(f"SQLite: {report['sqlite']['sqlite_version']} (FTS5={report['sqlite']['fts5_available']})")
        print(f"Offline Core Ready: {report['capabilities']['offline_core_ready']}")
        print(f"Browser Exploration Ready: {report['capabilities']['browser_exploration_ready']}")
        print(f"Semantic Vector Ready: {report['capabilities']['semantic_vector_ready']}")
    return EXIT_CODES["success"]


def _cmd_validate(args: argparse.Namespace) -> int:
    target = Path(args.file)
    errors = validate_file(args.kind, target)
    result = {
        "kind": args.kind,
        "file": target.as_posix(),
        "valid": len(errors) == 0,
        "errors": errors,
    }
    _emit_json(result)
    return EXIT_CODES["success"] if not errors else EXIT_CODES["contract_or_semantic_validation_failed"]


def _cmd_sanitize_check(args: argparse.Namespace) -> int:
    blocklist: list[str] = []
    if args.blocklist_file:
        bf = Path(args.blocklist_file)
        if not bf.is_file():
            sys.stderr.write(f"Error: blocklist file not found: {bf}\n")
            return EXIT_CODES["invalid_arguments"]
        blocklist = [
            line.strip()
            for line in bf.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    report = scan_directory(Path(args.target_dir), blocklist_terms=blocklist)
    _emit_json(report)
    return EXIT_CODES["success"] if report["clean"] else EXIT_CODES["sanitization_violation"]


def _cmd_run(args: argparse.Namespace) -> int:
    ws = Path(args.workspace)
    try:
        if args.run_action == "init":
            menus = args.menus.split(",") if args.menus else None
            state = init_workspace(
                workspace=ws,
                run_id=args.run_id,
                app_label=args.app_label,
                depth=args.depth,
                target_region=args.target_region,
                target_locale=args.target_locale,
                policy_version=args.policy_version,
                industry_label=args.industry,
                declared_menus=[m.strip() for m in menus if m.strip()] if menus else None,
            )
            _emit_json(state)
            return EXIT_CODES["success"]
        if args.run_action == "status":
            _emit_json(compute_workspace_status(ws))
            return EXIT_CODES["success"]
        if args.run_action == "resume":
            res = resume_workspace(ws)
            _emit_json(res)
            return (
                EXIT_CODES["success"]
                if res["safe_to_continue"]
                else EXIT_CODES["integrity_or_citation_or_readback_failed"]
            )
    except (FileNotFoundError, ContractValidationError, ValueError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_CODES["contract_or_semantic_validation_failed"]
    return EXIT_CODES["invalid_arguments"]


def _cmd_corpus(args: argparse.Namespace) -> int:
    from lingual_audit.corpus import add_web_evidence, build_sqlite_index, ingest_corpus

    try:
        if args.corpus_action == "add-web":
            domains = [tag.strip() for tag in args.domain.split(",") if tag.strip()]
            res = add_web_evidence(
                manifest_path=Path(args.manifest),
                url=args.url,
                title=args.title,
                excerpt=args.excerpt,
                en_term=args.en_term,
                zh_term=args.zh_term,
                domain_tags=domains,
                region_scope=args.region,
                target_locale=args.target_locale,
                policy_path=Path(args.policy) if args.policy else None,
                normative_level=args.normative_level,
                publisher=args.publisher,
                doc_version=args.doc_version,
            )
            _emit_json(res)
            return EXIT_CODES["success"]
        if args.corpus_action == "ingest":
            res = ingest_corpus(
                manifest_path=Path(args.manifest),
                output_dir=Path(args.output_dir),
            )
            _emit_json(res)
            return EXIT_CODES["success"]
        if args.corpus_action == "index":
            res = build_sqlite_index(
                normalized_dir=Path(args.normalized_dir),
                db_path=Path(args.db_path),
            )
            _emit_json(res)
            return EXIT_CODES["success"]
    except NotImplementedError as exc:
        sys.stderr.write(f"Not implemented yet: {exc}\n")
        return EXIT_CODES["optional_capability_missing"]
    except (FileNotFoundError, ContractValidationError, ValueError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_CODES["contract_or_semantic_validation_failed"]
    return EXIT_CODES["invalid_arguments"]


def _cmd_retrieve(args: argparse.Namespace) -> int:
    from lingual_audit.retrieval import retrieve_for_page

    try:
        bundles = retrieve_for_page(
            db_path=Path(args.db_path),
            page_inventory_path=Path(args.page_file),
            target_region=args.target_region,
            target_locale=args.target_locale,
            policy_path=Path(args.policy) if args.policy else None,
            top_k=args.top_k,
            mode=args.mode,
        )
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(bundles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _emit_json({"item_count": len(bundles), "bundles": bundles})
        return EXIT_CODES["success"]
    except NotImplementedError as exc:
        sys.stderr.write(f"Not implemented yet: {exc}\n")
        return EXIT_CODES["optional_capability_missing"]
    except (FileNotFoundError, ContractValidationError, ValueError) as exc:
        return _emit_command_error(exc)


def _cmd_review(args: argparse.Namespace) -> int:
    from lingual_audit.review import (
        check_cross_menu_consistency,
        prepare_review_packets,
        validate_reviews_against_index,
    )

    try:
        if args.review_action == "prepare":
            res = prepare_review_packets(
                db_path=Path(args.db_path),
                records_dir=Path(args.records_dir),
                output_path=Path(args.output),
                target_region=args.target_region,
                target_locale=args.target_locale,
                policy_path=Path(args.policy) if args.policy else None,
                mode=args.mode,
            )
            _emit_json(res)
            return EXIT_CODES["success"]
        if args.review_action == "validate":
            res = validate_reviews_against_index(
                review_path=Path(args.review_file),
                db_path=Path(args.db_path),
                records_dir=Path(args.records_dir) if args.records_dir else None,
                packets_path=Path(args.packets) if args.packets else None,
            )
            _emit_json(res)
            return EXIT_CODES["success"] if res.get("valid") else EXIT_CODES["integrity_or_citation_or_readback_failed"]
        if args.review_action == "consistency":
            res = check_cross_menu_consistency(review_path=Path(args.review_file))
            _emit_json(res)
            return EXIT_CODES["success"]
    except NotImplementedError as exc:
        sys.stderr.write(f"Not implemented yet: {exc}\n")
        return EXIT_CODES["optional_capability_missing"]
    except (FileNotFoundError, ContractValidationError, ValueError) as exc:
        return _emit_command_error(exc)
    return EXIT_CODES["invalid_arguments"]


def _cmd_export(args: argparse.Namespace) -> int:
    from lingual_audit.delivery import export_workbook

    try:
        manifest = export_workbook(
            menu_map_path=Path(args.menu_map),
            review_path=Path(args.review_file),
            output_xlsx=Path(args.output_xlsx),
            manifest_output=Path(args.manifest_output),
            include_summary_sheets=args.with_summary,
        )
        _emit_json(manifest)
        return (
            EXIT_CODES["success"]
            if manifest.get("readback_verified")
            else EXIT_CODES["integrity_or_citation_or_readback_failed"]
        )
    except NotImplementedError as exc:
        sys.stderr.write(f"Not implemented yet: {exc}\n")
        return EXIT_CODES["optional_capability_missing"]
    except (FileNotFoundError, ContractValidationError, ValueError) as exc:
        return _emit_command_error(exc)


def _cmd_verify(args: argparse.Namespace) -> int:
    from lingual_audit.delivery import verify_delivery

    try:
        report = verify_delivery(
            workbook_path=Path(args.workbook),
            manifest_path=Path(args.manifest),
            review_path=Path(args.review_file),
        )
        _emit_json(report)
        return (
            EXIT_CODES["success"]
            if report.get("verified")
            else EXIT_CODES["integrity_or_citation_or_readback_failed"]
        )
    except NotImplementedError as exc:
        sys.stderr.write(f"Not implemented yet: {exc}\n")
        return EXIT_CODES["optional_capability_missing"]
    except (FileNotFoundError, ContractValidationError, ValueError) as exc:
        return _emit_command_error(exc)


def _cmd_evaluate(args: argparse.Namespace) -> int:
    from lingual_audit.evaluate import run_golden_evaluation

    try:
        report = run_golden_evaluation(
            golden_set_path=Path(args.golden_set),
            db_path=Path(args.db_path),
            arm_name=args.arm,
            split=args.split,
            top_k=args.top_k,
            policy_path=Path(args.policy) if args.policy else None,
            predictions_path=Path(args.predictions) if args.predictions else None,
        )
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _emit_json(report)
        return EXIT_CODES["success"]
    except NotImplementedError as exc:
        sys.stderr.write(f"Not implemented yet: {exc}\n")
        return EXIT_CODES["optional_capability_missing"]
    except (FileNotFoundError, ContractValidationError, ValueError) as exc:
        return _emit_command_error(exc)


def _cmd_explore(args: argparse.Namespace) -> int:
    try:
        from lingual_audit.explore.runner import explore_target

        result = explore_target(
            url=args.url,
            workspace=Path(args.workspace),
            depth=args.depth,
            headless=args.headless,
        )
        _emit_json(result)
        return (
            EXIT_CODES["policy_or_authorization_blocked"]
            if result.get("status") in {"blocked", "skipped"}
            else EXIT_CODES["success"]
        )
    except ImportError as exc:
        sys.stderr.write(f"Optional capability missing: {exc}\n")
        return EXIT_CODES["optional_capability_missing"]
    except (FileNotFoundError, ContractValidationError, ValueError) as exc:
        return _emit_command_error(exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lingual-audit",
        description="Domain-agnostic bilingual UI audit, pluggable hybrid RAG terminology standardization, and Excel delivery CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Probe runtime capabilities.")
    p_doc.add_argument("--json", action="store_true", help="Emit JSON report.")
    p_doc.set_defaults(func=_cmd_doctor)

    p_val = sub.add_parser("validate", help="Validate JSON artifact against shared contracts.")
    p_val.add_argument("--kind", required=True, choices=sorted(SCHEMA_MAP.keys()))
    p_val.add_argument("file", help="Path to JSON artifact.")
    p_val.set_defaults(func=_cmd_validate)

    p_san = sub.add_parser("sanitize-check", help="Scan directory for secret paths and leaks.")
    p_san.add_argument("target_dir", help="Directory to scan.")
    p_san.add_argument("--blocklist-file", help="Optional newline-delimited forbidden terms file.")
    p_san.set_defaults(func=_cmd_sanitize_check)

    p_explore = sub.add_parser("explore", help="Discover rendered bilingual UI under read-only policy guardrails.")
    p_explore.add_argument("--url", required=True)
    p_explore.add_argument("--workspace", required=True)
    p_explore.add_argument("--depth", default="standard", choices=["quick", "standard", "thorough"])
    head_mode = p_explore.add_mutually_exclusive_group()
    head_mode.add_argument("--headless", dest="headless", action="store_true", default=True)
    head_mode.add_argument("--headed", dest="headless", action="store_false")
    p_explore.set_defaults(func=_cmd_explore)

    p_run = sub.add_parser("run", help="Manage external audit workspace checkpoints.")
    run_sub = p_run.add_subparsers(dest="run_action", required=True)

    p_rinit = run_sub.add_parser("init", help="Initialize workspace with empty BYOG corpus slot.")
    p_rinit.add_argument("--workspace", required=True)
    p_rinit.add_argument("--run-id", required=True)
    p_rinit.add_argument("--app-label", required=True)
    p_rinit.add_argument("--industry", default="general")
    p_rinit.add_argument("--depth", default="standard", choices=["quick", "standard", "thorough"])
    p_rinit.add_argument("--target-region", default="CN_MAINLAND")
    p_rinit.add_argument("--target-locale", default="zh-CN")
    p_rinit.add_argument("--policy-version", default="1.0.0")
    p_rinit.add_argument("--menus", help="Comma-separated declared menu names.")
    p_rinit.set_defaults(func=_cmd_run)

    p_rstat = run_sub.add_parser("status", help="Inspect workspace status.")
    p_rstat.add_argument("--workspace", required=True)
    p_rstat.set_defaults(func=_cmd_run)

    p_rres = run_sub.add_parser("resume", help="Safely resume interrupted workspace.")
    p_rres.add_argument("--workspace", required=True)
    p_rres.set_defaults(func=_cmd_run)

    p_corp = sub.add_parser("corpus", help="Ingest and index multi-format reference corpora.")
    corp_sub = p_corp.add_subparsers(dest="corpus_action", required=True)
    p_cweb = corp_sub.add_parser("add-web", help="Record a web excerpt in the local corpus cache.")
    p_cweb.add_argument("--manifest", required=True)
    p_cweb.add_argument("--url", required=True)
    p_cweb.add_argument("--title", required=True)
    p_cweb.add_argument("--excerpt", required=True)
    p_cweb.add_argument("--en-term")
    p_cweb.add_argument("--zh-term")
    p_cweb.add_argument("--domain", default="general_ui", help="Comma-separated domain tags.")
    p_cweb.add_argument("--region", default="GLOBAL")
    p_cweb.add_argument("--target-locale")
    p_cweb.add_argument("--policy")
    p_cweb.add_argument("--normative-level")
    p_cweb.add_argument("--publisher")
    p_cweb.add_argument("--doc-version")
    p_cweb.set_defaults(func=_cmd_corpus)
    p_cing = corp_sub.add_parser("ingest", help="Parse corpus manifest into normalized JSONL artifacts.")
    p_cing.add_argument("--manifest", required=True)
    p_cing.add_argument("--output-dir", required=True)
    p_cing.set_defaults(func=_cmd_corpus)
    p_cidx = corp_sub.add_parser("index", help="Build rebuildable SQLite FTS5 index.")
    p_cidx.add_argument("--normalized-dir", required=True)
    p_cidx.add_argument("--db-path", required=True)
    p_cidx.set_defaults(func=_cmd_corpus)

    p_ret = sub.add_parser("retrieve", help="Retrieve controlled evidence bundles for a page inventory.")
    p_ret.add_argument("--db-path", required=True)
    p_ret.add_argument("--page-file", required=True)
    p_ret.add_argument("--target-region", default="CN_MAINLAND")
    p_ret.add_argument("--target-locale", default="zh-CN")
    p_ret.add_argument("--policy", help="Optional authority_policy.json path.")
    p_ret.add_argument("--top-k", type=int, default=8)
    p_ret.add_argument("--mode", default="glossary_fts", choices=["glossary_fts", "hybrid_vector", "hybrid_vector_rerank"])
    p_ret.add_argument("--output")
    p_ret.set_defaults(func=_cmd_retrieve)

    p_rev = sub.add_parser("review", help="Prepare, validate, and audit structured reviews.")
    rev_sub = p_rev.add_subparsers(dest="review_action", required=True)
    p_rprep = rev_sub.add_parser("prepare", help="Generate controlled evidence packets for all page inventories.")
    p_rprep.add_argument("--db-path", required=True)
    p_rprep.add_argument("--records-dir", required=True)
    p_rprep.add_argument("--output", required=True)
    p_rprep.add_argument("--target-region", default="CN_MAINLAND")
    p_rprep.add_argument("--target-locale", default="zh-CN")
    p_rprep.add_argument("--policy", help="Optional authority_policy.json path.")
    p_rprep.add_argument("--mode", default="glossary_fts")
    p_rprep.set_defaults(func=_cmd_review)

    p_rval = rev_sub.add_parser("validate", help="Verify review schema and citation integrity against SQLite index.")
    p_rval.add_argument("--review-file", required=True)
    p_rval.add_argument("--db-path", required=True)
    p_rval.add_argument("--records-dir", required=True)
    p_rval.add_argument("--packets", help="Optional prepared packets.json path.")
    p_rval.set_defaults(func=_cmd_review)

    p_rcon = rev_sub.add_parser("consistency", help="Run concept-scoped cross-menu consistency audit.")
    p_rcon.add_argument("--review-file", required=True)
    p_rcon.set_defaults(func=_cmd_review)

    p_exp = sub.add_parser("export", help="Export 6-column color-coded Excel workbook and delivery manifest.")
    p_exp.add_argument("--menu-map", required=True)
    p_exp.add_argument("--review-file", required=True)
    p_exp.add_argument("--output-xlsx", required=True)
    p_exp.add_argument("--manifest-output", required=True)
    p_exp.add_argument("--with-summary", action="store_true")
    p_exp.set_defaults(func=_cmd_export)

    p_ver = sub.add_parser("verify", help="Verify Excel workbook read-back and row hashes.")
    p_ver.add_argument("--workbook", required=True)
    p_ver.add_argument("--manifest", required=True)
    p_ver.add_argument("--review-file", required=True)
    p_ver.set_defaults(func=_cmd_verify)

    p_eval = sub.add_parser("evaluate", help="Evaluate retrieval/review quality against golden set.")
    p_eval.add_argument("--golden-set", required=True)
    p_eval.add_argument("--db-path", required=True)
    p_eval.add_argument("--arm", default="glossary_fts")
    p_eval.add_argument("--split", default="all", choices=["all", "dev", "holdout"])
    p_eval.add_argument("--top-k", type=int, default=8)
    p_eval.add_argument("--policy", help="Optional authority_policy.json path.")
    p_eval.add_argument("--predictions")
    p_eval.add_argument("--output")
    p_eval.set_defaults(func=_cmd_evaluate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
