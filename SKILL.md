---
name: lingual-audit
description: "Domain-agnostic, self-contained workflow for bilingual UI inventory, Bring-Your-Own-Glossary (BYOG) + web-assisted hybrid RAG terminology standardization, citation-verified review, and incremental 6-column color-coded Excel delivery on complex web systems. Triggers: bilingual UI audit, localization review, terminology standardization, 中英规范化, 界面词条审译, 双语校对."
---

# Lingual Audit

Inventory the bilingual UI (source English and target locale) of a complex web system, review terminology using local domain glossaries, web-sourced evidence, and LLM language judgment, and export a verified 6-column color-coded Excel workbook incrementally module by module.

This skill is self-contained and domain-agnostic. It does not vendor third-party skills (`app-docs`, `planning-with-files`, etc.). If those skills are already installed in your host environment, you may coordinate with them; otherwise, follow the protocols in `references/` directly.

## Safety and integrity rules

1. **External workspace only**: Store all runtime files (`run.json`, `records/`, `corpus/`, `deliverables/`) in a user-specified external directory (`--workspace`), never inside this skill repository.
2. **Read-only by default**: Collect only translatable UI labels, prompts, tooltips, headers, and confirmed static options. Exclude dynamic business rows, candidate entity records, user inputs, cookies, and credentials.
3. **Network isolation for `thorough` input probes**: In `thorough` mode, entering synthetic test strings to trigger client-side validation messages is allowed only inside a Playwright context (`playwright>=1.48`) with `service_workers="block"` and outbound HTTP and WebSocket traffic blocked until that context is destroyed. Never click save, submit, execute, approve, delete, import, or export buttons. If the host cannot enforce network isolation, skip synthetic input probes and record the unit as skipped or blocked.
4. **No fabricated citations and no middle-ground verdicts**: Cite only `evidence_id` entries present in that item's prepared `packets.json` bundle, and never let English-only definitions (`en_only` or `concept_definition_only`) authorize target-language translations. Reserve `待核对` strictly for items whose bilingual UI text was not actually verified; once both languages are verified, decide clearly between `保留` and `建议修改` (aside from `缺译`, `单语限制`, and `来源冲突`).
5. **Persist and append after each module**: As soon as one feature module (menu) is collected and reviewed, write its `records/pages/<page_id>.json`, merge its decisions into `records/review.json`, and re-run `lingual-audit export` so the deliverable workbook grows one completed module at a time.

## Workflow

1. **Probe and initialize**:
   - Run `lingual-audit doctor --json` to check Python, SQLite FTS5, parsers, `openpyxl`, and optional Playwright availability.
   - Run `lingual-audit run init --workspace <dir> --run-id <RUN-YYYYMMDD-NNNN> --app-label "<App>" --industry "<domain>" --depth <quick|standard|thorough> --target-region <REGION> --target-locale <LOCALE>`.
   - This creates an empty BYOG corpus slot (`corpus/raw/`, `corpus/manifest.json`, `corpus/authority_policy.json`) and human-readable handoff files (`task_plan.md`, `findings.md`, `progress.md`).
2. **Collect bilingual UI strings (module by module)**:
   - Follow [Exploration & Bilingual Collection](references/exploration.md) and [Evidence & Checkpoints](references/evidence-and-checkpoints.md).
   - Record verified browser observations into `records/menu-map.json` and `records/pages/<page_id>.json` after each module, validating each file with `lingual-audit validate`. You may also run the experimental CLI explorer `lingual-audit explore --url <URL> --workspace <dir> --depth <depth>` on pages that expose semantic attributes (`[data-menu]`, `[data-i18n]`, `placeholder`).
3. **Ingest and index the BYOG corpus (plus optional L2 web evidence)**:
   - Place your domain's PDF, HTML, XLSX, or CSV glossaries and standards into `<workspace>/corpus/raw/`, declare them in `<workspace>/corpus/manifest.json`, and tune `<workspace>/corpus/authority_policy.json` (including optional `web_domain_tiers`).
   - Whenever the local glossary misses a term or needs disambiguation, search the web with the host agent and record each verified web source locally: `lingual-audit corpus add-web --manifest <ws>/corpus/manifest.json --url <URL> --title "<Title>" --excerpt "<Verbatim excerpt>" --en-term "<EN>" --zh-term "<ZH>" --policy <ws>/corpus/authority_policy.json`.
   - Run `lingual-audit corpus ingest --manifest <ws>/corpus/manifest.json --output-dir <ws>/corpus/normalized`
   - Run `lingual-audit corpus index --normalized-dir <ws>/corpus/normalized --db-path <ws>/corpus/index.sqlite`
   - An empty manifest is valid: retrieval returns `no_authoritative_hit=True` without failing.
4. **Run three-tier review and packet-bound verification**:
   - Follow [RAG Review & Excel Delivery](references/rag-review-and-delivery.md).
   - Build controlled evidence packets: `lingual-audit review prepare --db-path <ws>/corpus/index.sqlite --records-dir <ws>/records --output <ws>/records/packets.json --policy <ws>/corpus/authority_policy.json`.
   - Review each module using the L1 (local glossary) > L2 (cached web evidence) > L3 (LLM language judgment) hierarchy, merging completed module decisions into `<ws>/records/review.json` with a business-first `business_rationale` for every item whose verdict is not `保留`.
   - Run `lingual-audit review validate --review-file <ws>/records/review.json --db-path <ws>/corpus/index.sqlite --records-dir <ws>/records --packets <ws>/records/packets.json`
   - Run `lingual-audit review consistency --review-file <ws>/records/review.json` and merge `consistency_findings` into `review.json`.
5. **Incremental 6-column Excel export and read-back**:
   - After each completed module, run `lingual-audit export --menu-map <ws>/records/menu-map.json --review-file <ws>/records/review.json --output-xlsx <ws>/deliverables/audit.xlsx --manifest-output <ws>/deliverables/delivery-manifest.json` (pass `--with-summary` to append `_Summary` and `_Consistency` sheets).
   - Run `lingual-audit verify --workbook <ws>/deliverables/audit.xlsx --manifest <ws>/deliverables/delivery-manifest.json --review-file <ws>/records/review.json`.
   - Continue to the next module, append its page inventory and review records, and re-export so completed work is always saved on disk.
6. **Optional evaluation and release hygiene**:
   - Run `lingual-audit evaluate --golden-set <path> --db-path <ws>/corpus/index.sqlite --arm glossary_fts --split holdout`.
   - Before sharing files externally, run `lingual-audit sanitize-check <dir>`.

## Reference documents

- Route and control discovery, depth modes, and `thorough` network isolation: [references/exploration.md](references/exploration.md)
- State ledger, observation and coverage enums, incremental module delivery, and crash-safe resume: [references/evidence-and-checkpoints.md](references/evidence-and-checkpoints.md)
- BYOG + web three-tier review, applicability arbitration, and 6-column Excel format: [references/rag-review-and-delivery.md](references/rag-review-and-delivery.md)
- Starter templates: [templates/](templates)
