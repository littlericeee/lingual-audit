<div align="center">

# lingual-audit

Domain-agnostic bilingual UI audit Agent Skill and local hybrid RAG CLI for complex web systems

[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-Self--Contained-blue.svg)](SKILL.md)
[![LLM Agnostic](https://img.shields.io/badge/LLM-Agnostic-purple.svg)](#works-with-any-ai-host)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](pyproject.toml)
[![Open Source](https://img.shields.io/badge/Open%20Source-MIT-green.svg)](LICENSE)

<br>

[中文](README.md) · [Comparison](#how-it-differs-from-raw-llm-review) · [What it does](#what-it-does) · [How it works](#how-it-works) · [Excel format](#excel-output-format) · [Quick start](#quick-start) · [Contributing](CONTRIBUTING.md)

</div>

`lingual-audit` is a domain-agnostic Agent Skill and Python CLI for auditing bilingual UI copy on complex web systems. It collects visible English and target-language strings from a running web app, checks them against your own glossaries and web-sourced standards using local hybrid retrieval, and exports a color-coded Excel workbook with packet-verified citations.

Complex web apps in finance, healthcare, manufacturing, SaaS, e-commerce, or developer tooling tend to accumulate thousands of inconsistent labels, mistranslations, and raw i18n keys across hundreds of views. Auditing them by hand takes weeks. Handing reference PDFs straight to an LLM is faster, but models miss short terms, invent citations, drift across menus, and can accidentally trigger auto-save requests while clicking around forms. `lingual-audit` splits the job in two: the LLM handles language judgment, while deterministic Python tools handle retrieval, citation verification, and Excel export.

## How it differs from raw LLM review

| Handing reference PDFs straight to an LLM | `lingual-audit` |
|---|---|
| Short 2 to 3 letter acronyms and short CJK labels are easy to miss | Exact acronym channel + SQLite FTS5 + CJK bigram local index |
| Models sometimes invent page numbers or cite unrelated entries | `review validate` binds every citation to that item's `packets.json` bundle |
| English-only definitions get used to justify Chinese UI labels | Applicability-first filtering blocks EN-only sources from authorizing target copy |
| Waiting until the whole app finishes risks losing hours of work | Writes and exports incrementally after every completed feature module |
| Spreadsheet formulas and raw observed copy are hard to audit | 6-column color-coded plain-text cells with automatic `openpyxl` read-back checks |

## What it does

- **Bring your own glossary (BYOG) + web evidence cache**: The workspace starts with an empty knowledge base slot (`corpus/raw/`, `corpus/manifest.json`, `corpus/authority_policy.json`). Drop in your own `PDF`, `HTML`, `XLSX`, or `CSV` glossaries for any industry without editing code. When the local glossary misses a term, the host agent can search the web and save the URL and verbatim excerpt with `lingual-audit corpus add-web` so web evidence flows through the same hash-checked index and citation verifier.
- **Three-tier evidence order**: Reviews check L1 local glossaries first, fall back to L2 cached web evidence (`corpus add-web`, ranked by optional `web_domain_tiers`), and use L3 LLM language judgment when neither provides an external bilingual standard (labeled `[LLM 语义判断]` in the rationale column).
- **Incremental per-module delivery**: Finish one feature module, write its page inventory and review decisions to disk immediately, and re-export the Excel workbook so each completed module appends its sheet right away instead of waiting for hundreds of pages to finish.
- **Local SQLite FTS5 retrieval with packet-bound citation checks**: Parsers extract both verbatim and NFKC-normalized text from multi-format documents, build an exact short-acronym channel (`FX`, `IRS`, `BIC`) alongside a CJK bigram index, and filter sources by domain, region, and target locale before comparing normative precedence. `review validate` checks every citation against `packets.json` so missing or unrelated citations are rejected.
- **Experimental Playwright explorer with network isolation**: `lingual-audit explore` (experimental; requires `playwright>=1.48`) supports `quick`, `standard`, and `thorough` depths. It relies on semantic attributes (`[data-menu]`, `[data-i18n]`, `placeholder`) and marks pages with zero matched items as `blocked` rather than `fully_inspected`. For custom DOMs, record observations with your host browser tool or start from JSON inventory fixtures.
- **Color-coded, formula-safe Excel export**: Exports one worksheet per menu (`页面层级 | 系统英文 | 系统中文 | AI 建议中文 | 比对结论 | 修改理由与业务依据`), with 6 distinct verdict colors, a required business-focused rationale for every non-`保留` row, plain-text formula protection, and automatic `openpyxl` read-back checks.

## How it works

```mermaid
flowchart TD
    A["Target Complex Web System (EN / Target Locale)"] --> B["Host Browser Capture or Experimental Explorer (quick / standard / thorough)"]
    B --> C["External Workspace Ledger (run.json + records/menu-map.json + pages/*.json)"]
    D["L1 BYOG Corpus (PDF / HTML / XLSX / CSV) + L2 Web Cache (corpus add-web)"] --> E["Deterministic Parsers (pdfplumber / BS4 / openpyxl / CSV + NFKC)"]
    E --> F["Rebuildable SQLite Index (glossary_entries + document_chunks + FTS5 + CJK bigrams)"]
    C --> G["Context-Aware Retrieval (UI Hierarchy + Control + Siblings + Domain)"]
    G --> F
    F --> H["Controlled Evidence Bundles (packets.json + Applicability + Conflict Flags)"]
    H --> I["Host LLM Review (L1 Local > L2 Web > L3 Language Judgment)"]
    I --> J["Packet-Bound Citation Verifier & Concept-Scoped Consistency Audit"]
    J --> K["Incremental 6-Column Color-Coded Formula-Safe Excel + delivery-manifest.json"]
    K --> L["Automatic openpyxl Read-Back Verification"]
```

## Excel output format

| Column | Header | Purpose |
|---|---|---|
| A | `页面层级` | Full UI hierarchy (`Menu > Page State > Section > Control`) |
| B | `系统英文` | Verbatim observed English UI text (explicit text cell, formula-safe) |
| C | `系统中文` | Verbatim observed target-language UI text |
| D | `AI 建议中文` | Suggested translation (blank when `保留` or unverified) |
| E | `比对结论` | Color-coded verdict (`保留`, `建议修改`, `缺译`, `来源冲突`, `单语限制`, `待核对`) |
| F | `修改理由与业务依据` | Business-first rationale with brief provenance tag (required whenever verdict ≠ `保留`) |

`待核对` is reserved strictly for items whose bilingual UI text was not actually verified (such as unpaired controls, single-side observations, or blocked pages). Once bilingual text is verified, decide clearly between `保留` and `建议修改` (aside from `缺译`, `单语限制`, and `来源冲突`).

## Quick start

### 1. Install and check your environment

```bash
# Create and activate an isolated virtualenv (works as editable source install or standalone wheel)
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1

python -m pip install -e ".[dev,browser]"

# Required before running `explore` or the full browser test suite
python -m playwright install chromium

lingual-audit doctor --json
```

### 2. Run the end-to-end synthetic demo (from corpus to verified Excel)

This repo includes sanitized synthetic fixtures under `tests/fixtures/` so you can run the full workflow offline and inspect the generated workbook right away:

```bash
mkdir -p /tmp/lingual-audit-demo

# Ingest the synthetic corpus and build the SQLite FTS5 index
.venv/bin/lingual-audit corpus ingest --manifest tests/fixtures/corpus/manifest.json --output-dir /tmp/lingual-audit-demo/normalized
.venv/bin/lingual-audit corpus index --normalized-dir /tmp/lingual-audit-demo/normalized --db-path /tmp/lingual-audit-demo/index.sqlite

# Prepare controlled evidence packets for the sample UI inventories
.venv/bin/lingual-audit review prepare --db-path /tmp/lingual-audit-demo/index.sqlite --records-dir tests/fixtures/records --output /tmp/lingual-audit-demo/packets.json --policy tests/fixtures/corpus/authority_policy.json

# Simulate the host LLM review step using the offline demo helper
.venv/bin/python3 examples/build_demo_review.py /tmp/lingual-audit-demo/packets.json /tmp/lingual-audit-demo/review.json

# Run packet-bound citation validation and cross-menu consistency checks
.venv/bin/lingual-audit review validate --review-file /tmp/lingual-audit-demo/review.json --db-path /tmp/lingual-audit-demo/index.sqlite --records-dir tests/fixtures/records --packets /tmp/lingual-audit-demo/packets.json
.venv/bin/lingual-audit review consistency --review-file /tmp/lingual-audit-demo/review.json

# Export the 6-column color-coded Excel workbook and verify read-back
.venv/bin/lingual-audit export --menu-map tests/fixtures/records/menu-map.json --review-file /tmp/lingual-audit-demo/review.json --output-xlsx /tmp/lingual-audit-demo/audit.xlsx --manifest-output /tmp/lingual-audit-demo/delivery-manifest.json --with-summary
.venv/bin/lingual-audit verify --workbook /tmp/lingual-audit-demo/audit.xlsx --manifest /tmp/lingual-audit-demo/delivery-manifest.json --review-file /tmp/lingual-audit-demo/review.json
```

Open `/tmp/lingual-audit-demo/audit.xlsx` to inspect the color-coded menu sheets, `_Summary`, and `_Consistency`. The final `verify` command outputs `"verified": true`.

### 3. Initialize your own BYOG workspace (optional)

```bash
# Initialize an external workspace with an empty BYOG corpus slot
.venv/bin/lingual-audit run init \
  --workspace /tmp/my-audit-ws \
  --run-id RUN-20261127-0001 \
  --app-label "My Web System" \
  --industry "general_web" \
  --depth standard

# Optionally record web-searched excerpts whenever your local glossary misses a term
.venv/bin/lingual-audit corpus add-web \
  --manifest /tmp/my-audit-ws/corpus/manifest.json \
  --url "https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Idempotency-Key" \
  --title "Idempotency-Key" \
  --en-term "Idempotency Key" \
  --zh-term "幂等键" \
  --excerpt "Idempotency Key ensures repeated requests have the same effect as a single request." \
  --policy /tmp/my-audit-ws/corpus/authority_policy.json

# Run deterministic test suite
.venv/bin/pytest -v -p no:anyio
```

## Works with any AI host

This repository ships a self-contained Agent Skill ([SKILL.md](SKILL.md), [references/](references), [templates/](templates)) with zero vendored third-party skills:

- **Claude Code, Codex, Antigravity, WorkBuddy**: Load the repo directly or copy `SKILL.md`, `references/`, and `templates/` into your host's skills directory.
- **Any other LLM or agent framework**: Both the workflow and CLI are model-agnostic. Point any assistant at [SKILL.md](SKILL.md) and [references/](references), and run `lingual-audit` locally for deterministic indexing, citation checks, and Excel export.

## Design trade-offs

**Screen for applicability before ranking authority.**
In multi-region products, a `zh-HK` label and a `zh-CN` standard are often valid regional variants, and an English-only international standard explains what a concept means without authorizing a Chinese UI label. Checking `(domain, region, target_locale, language_scope)` before comparing `normative_precedence` avoids false overrides and keeps real source conflicts visible.

**Start with exact glossaries, SQLite FTS5, and CJK bigrams before adding dense vectors.**
UI strings are short: 2 to 3 letter acronyms or two-character Chinese terms. Embeddings drift on short labels, and SQLite's built-in `trigram` tokenizer cannot match queries shorter than three characters. A single-file SQLite index with exact channels and CJK bigrams runs offline with no external services or API keys, and produces repeatable test results. An RRF vector hook is included for teams that want to benchmark hybrid embeddings against a Golden Set.

**Isolate `thorough` input probes in a throwaway Playwright context.**
Skipping the Submit button does not prevent backend writes. Modern web forms routinely send `POST` or `PUT` auto-save requests on `input`, `change`, `blur`, or debounced timers. Blocking HTTP and WebSocket traffic (`playwright>=1.48`) for the entire lifetime of an isolated probe context and destroying that context afterward prevents any outbound write. Hosts without request interception skip synthetic input probes automatically.

**Separate the reviewer spreadsheet from the verification manifest.**
Reviewers want a clean sheet they can scan by color, with the UI path on the left and a clear business reason on the right. Engineering verification needs SHA-256 hashes and structured citation IDs. Putting human-readable decisions in the 6-column workbook and machine-checked provenance in `review.json` and `delivery-manifest.json` keeps both files clean.

## Contributing

Issues and pull requests are welcome, especially parser improvements, domain-agnostic golden-set fixtures, browser capture work, and translations. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, test commands, and sanitization rules.

## License

Released under the [MIT License](LICENSE).

Inspired by route-discovery conventions from `app-docs` and disk-backed session checkpointing from `planning-with-files`. Neither skill is vendored into this repository; all exploration, checkpointing, retrieval, and delivery code is implemented here under the MIT license.
