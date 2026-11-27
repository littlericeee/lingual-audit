# enterprise-i18n-ops

An enterprise-grade, **domain-agnostic**, self-contained Agent Skill and deterministic Python CLI toolchain for **bilingual UI inventory, pluggable tiered hybrid RAG terminology standardization, and auditable Excel delivery** on complex B2B web systems.

> **Status:** Phase 0 domain-agnostic contracts, CLI core, and sanitized fixtures complete; Phase 1 corpus/RAG/delivery implementation in progress.

## Bring-Your-Own-Glossary (BYOG) — Works for Any Industry

This repository ships with **zero hardcoded industry assumptions** and an **empty default knowledge base slot**:

- Running `i18n-ops run init` creates a clean workspace with `corpus/raw/`, an empty `corpus/manifest.json`, and an editable `corpus/authority_policy.json`.
- Drop **any industry's** bilingual glossaries, regulatory PDFs, HTML guidelines, or XLSX/CSV field dictionaries (FinTech, Healthcare/EHR, Manufacturing/MES, Legal, Supply Chain/ERP, SaaS, etc.) into `corpus/raw/`, declare their open `domain_tags` and `region_scope` in `manifest.json`, and run `corpus ingest` + `corpus index`.
- Even with an empty corpus, the retrieval engine degrades cleanly (`no_authoritative_hit=True`) without crashing or fabricating citations.
- Synthetic financial fixtures under `tests/fixtures/` serve strictly as one pluggable test pack demonstrating hard terminology edge cases (short acronyms, cross-domain polysemy, regional variants, EN-only definitions).

## Architecture Overview

1. **Zero-Vendor Self-Contained Bilingual Exploration (`SKILL.md` + `references/`)**:
   - Absorbs universal route/state discovery and file-based session recovery natively (no vendored third-party skills; optionally pairs with `app-docs`, `planning-with-files`, and `playwright` when present).
   - Supports `quick` / `standard` / `thorough` depths. In `thorough` mode, synthetic test-data probing to trigger client-side validation messages is gated by **Playwright-enforced network isolation** (Service Worker disabled; HTTP & WebSocket outbound blocked until state reset); hosts without network isolation automatically skip synthetic input probes.
2. **Rebuildable Local Knowledge Base & Applicability-First Retrieval**:
   - Parses heterogeneous reference corpora (`PDF` via `pdfplumber`, `HTML`, `XLSX`, `CSV`) into dual verbatim/normalized views stored in single-file **SQLite + FTS5** (with CJK bigram + exact short-acronym channels).
   - Evaluates authority via **config-driven applicability filtering first** (`domain`, `region`, `target_locale`, `version`, `effective_date`), then intra-scope precedence—preserving legitimate regional variants and never allowing English-only definitions to masquerade as target-language translations.
   - Optional dense vector + RRF retrieval and reranking are evaluated via 3-arm Golden Set ablations (`V1 doc-browsing` vs `Glossary + FTS5` vs `Hybrid Vector`).
3. **6-Column Color-Coded Excel Delivery & Traceability**:
   - Per-menu sheets with 6 fixed columns: `页面层级 | 系统英文 | 系统中文 | AI 建议中文 | 比对结论 | 修改理由与业务依据`.
   - Distinct cell fill colors per verdict (`保留`, `建议修改`, `缺译`, `来源冲突`, `单语限制`, `待业务确认`, `待核对`), mandatory business-first rationale for non-`保留` items, formula-injection protection, mandatory `openpyxl` read-back verification, and full structured provenance in companion `review.json` / `delivery-manifest.json`.

## Quick Start (Development)

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/i18n-ops doctor --json
.venv/bin/pytest
```

## Acknowledgments

Inspired by route-discovery patterns from `app-docs` and disk-backed session checkpointing from `planning-with-files`. Neither package is vendored into this repository.
