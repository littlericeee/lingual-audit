---
name: enterprise-i18n-ops
description: "Domain-agnostic, self-contained workflow for bilingual UI inventory, Bring-Your-Own-Glossary (BYOG) hybrid RAG terminology standardization, citation-verified review, and 6-column color-coded Excel delivery on complex B2B web systems. Triggers: bilingual UI audit, localization review, terminology standardization, 中英规范化, 界面词条审译, 双语校对."
---

# Enterprise i18n Ops

Systematically inventory a B2B web application's bilingual UI (source English & target locale), standardize terminology against your own domain glossaries and standards via deterministic hybrid retrieval, and deliver a verified, 6-column color-coded Excel audit workbook.

This skill is **100% self-contained and domain-agnostic**. It does **not** vendor third-party skills (`app-docs`, `planning-with-files`, etc.); if those companion skills are installed in your host environment, you may optionally coordinate with them, and if not, follow the built-in protocols in `references/` directly.

## Hard Safety & Integrity Boundaries

1. **External Workspace Only**: Store all runtime records (`run.json`, `records/`, `corpus/`, `deliverables/`) in a user-specified external workspace directory (`--workspace`), never inside this skill repository.
2. **Read-Only by Default**: Collect only translatable UI labels, prompts, tooltips, headers, and confirmed static options. Exclude dynamic business rows, candidate entity records, user inputs, cookies, and credentials.
3. **Thorough Synthetic Input Guardrail**: In `thorough` mode, entering synthetic test data to trigger client-side validation messages is permitted **only** under Playwright-enforced network isolation (`service_workers="block"`, HTTP & WebSocket outbound blocked until clean reset is verified). Never click save, submit, execute, approve, delete, import, or export buttons. If the current host cannot enforce network isolation, skip synthetic input probes and record the unit as skipped/blocked.
4. **No Fabricated Citations**: During review, cite **only** `evidence_id` entries present in the controlled retrieval bundle. Never allow English-only definitions (`en_only` / `concept_definition_only`) to authorize target-language translations.

## End-to-End Workflow

1. **Probe & Initialize**:
   - Run `i18n-ops doctor --json` to verify Python, SQLite FTS5, parsers, `openpyxl`, and optional Playwright availability.
   - Run `i18n-ops run init --workspace <dir> --run-id <RUN-YYYYMMDD-NNNN> --app-label "<App>" --industry "<domain>" --depth <quick|standard|thorough> --target-region <REGION> --target-locale <LOCALE>`.
   - This initializes an empty BYOG corpus slot (`corpus/raw/`, `corpus/manifest.json`, `corpus/authority_policy.json`) and file-based handoff views (`task_plan.md`, `findings.md`, `progress.md`).
2. **Bilingual UI Discovery**:
   - Follow [Exploration & Bilingual Collection](references/exploration.md) and [Evidence & Checkpoints](references/evidence-and-checkpoints.md).
   - Run `i18n-ops explore --url <URL> --workspace <dir> --depth <depth>` (or record verified browser observations directly into `records/menu-map.json` and `records/pages/<page_id>.json`, validating each with `i18n-ops validate`).
3. **BYOG Corpus Ingestion & Indexing**:
   - Place any industry's PDF, HTML, XLSX, or CSV glossaries/standards into `<workspace>/corpus/raw/`, declare them in `<workspace>/corpus/manifest.json`, and tune `<workspace>/corpus/authority_policy.json` if needed.
   - Run `i18n-ops corpus ingest --manifest <ws>/corpus/manifest.json --output-dir <ws>/corpus/normalized`
   - Run `i18n-ops corpus index --normalized-dir <ws>/corpus/normalized --db-path <ws>/corpus/index.sqlite`
   - Empty manifests are valid: retrieval degrades cleanly (`no_authoritative_hit=True`) without crashing.
4. **Controlled RAG Review & Verification**:
   - Follow [RAG Review & Excel Delivery](references/rag-review-and-delivery.md).
   - Generate controlled evidence packets: `i18n-ops review prepare --db-path <ws>/corpus/index.sqlite --records-dir <ws>/records --output <ws>/records/packets.json --policy <ws>/corpus/authority_policy.json`.
   - Produce `<ws>/records/review.json` grounded strictly in each item's `evidence_bundle`, writing a business-first `business_rationale` for every item whose verdict is not `保留`.
   - Run `i18n-ops review validate --review-file <ws>/records/review.json --db-path <ws>/corpus/index.sqlite --records-dir <ws>/records`
   - Run `i18n-ops review consistency --review-file <ws>/records/review.json` and merge `consistency_findings` into `review.json`.
5. **6-Column Color-Coded Excel Export & Read-Back**:
   - Run `i18n-ops export --menu-map <ws>/records/menu-map.json --review-file <ws>/records/review.json --output-xlsx <ws>/deliverables/audit.xlsx --manifest-output <ws>/deliverables/delivery-manifest.json` (add `--with-summary` to append `_Summary` and `_Consistency` sheets).
   - Run `i18n-ops verify --workbook <ws>/deliverables/audit.xlsx --manifest <ws>/deliverables/delivery-manifest.json --review-file <ws>/records/review.json`.
6. **Optional Evaluation & Release Hygiene**:
   - Run `i18n-ops evaluate --golden-set <path> --db-path <ws>/corpus/index.sqlite --arm glossary_fts --split holdout`.
   - Before sharing artifacts externally, run `i18n-ops sanitize-check <dir>`.

## Reference Documents (Read on Demand)

- Route/control discovery, depth modes, and `thorough` network isolation: [references/exploration.md](references/exploration.md)
- State ledger, observation/coverage enums, and crash-safe resume: [references/evidence-and-checkpoints.md](references/evidence-and-checkpoints.md)
- BYOG corpus policy, RAG applicability arbitration, and 6-column Excel specification: [references/rag-review-and-delivery.md](references/rag-review-and-delivery.md)
- Starter templates: [templates/](templates)
