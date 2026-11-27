# BYOG Hybrid RAG Review & 6-Column Excel Delivery

## 1. Bring-Your-Own-Glossary (BYOG) Corpus Setup

`enterprise-i18n-ops` is **industry-agnostic** and ships with an empty default knowledge base slot. To audit any industry's system (finance, healthcare/EHR, manufacturing/MES, legal, supply chain, SaaS):

1. Drop your reference documents (`PDF`, `HTML`, `XLSX`, `CSV`) into `<workspace>/corpus/raw/`.
2. Register each document in `<workspace>/corpus/manifest.json` with open `domain_tags` (e.g. `clinical_trial`, `warehouse_wms`, `fx`), `region_scope` (e.g. `CN_MAINLAND`, `HK`, `US`, `EU`, `GLOBAL`), `target_locale` (e.g. `zh-CN`, `zh-HK`), `normative_level`, and `language_scope` (`bilingual_en_zh` | `zh_only` | `en_only`).
3. Customize `<workspace>/corpus/authority_policy.json` to define your industry's `normative_precedence`, `region_fallback_order`, `domain_hierarchy`, and `conflict_rules`.
4. Run `i18n-ops corpus ingest` and `i18n-ops corpus index`.

> **Empty Corpus Fallback**: If `documents` is empty, ingestion and indexing still succeed cleanly, and retrieval emits `candidates: []` with `conflict_flags.no_authoritative_hit = true` so the review never crashes or fabricates citations.

## 2. Applicability-First Hybrid Retrieval & Conflict Arbitration

Retrieval (`i18n-ops retrieve` / `i18n-ops review prepare`) combines:

1. **Exact & Alias Glossary Channel (`exact_glossary` / `alias_glossary`)**: NFKC-normalized exact matching over terms, aliases, and field codes—reliably resolving 1–3 char acronyms (e.g., `FX`, `IRS`, `BIC`) and preserving all polysemous candidates.
2. **Context-Aware FTS5 + CJK Bigram Channel (`fts_bm25` / `cjk_bigram`)**: Queries SQLite FTS5 using `en_text`, `hierarchy_path`, `control_type`, `sibling_labels`, and `domain_tag`. Existing UI target text is used only as a weak secondary tie-breaker so mistranslations never hijack search.
3. **Optional Dense Vector + RRF Channel (`dense_vector`)**: Extensible Phase 3 hook fusing lexical and semantic ranks via Reciprocal Rank Fusion when embeddings are present; falls back cleanly to `glossary_fts` when absent.

### Screening Before Ranking

Sources are screened for **applicability first** (`domain` + `domain_hierarchy`, `region_scope` + `region_fallback_order`, `target_locale`, `language_scope`) before comparing `normative_precedence`:

- `in_scope`: Matches target domain, region/fallback, and bilingual support.
- `regional_variant`: Matches domain with bilingual support, but belongs to another region/locale (preserved as a legitimate regional variant rather than silently overwritten).
- `en_definition_only`: English-only standard/definition (`support_type == "concept_definition_only"`). Explains meaning only—**forbidden from authorizing a `建议修改` Chinese translation**.
- `cross_domain_reference`: Matches region but different domain (prevents cross-domain polysemy collisions).
- `same_scope_conflict`: Triggered whenever 2+ distinct target translations appear among `in_scope` bilingual sources; surface both citations and mark `来源冲突`.

## 3. Controlled Review, Citation Integrity & Consistency Audit

- The host LLM receives each item's `ui_item` + `evidence_bundle` (`records/packets.json`) and may cite **only** `evidence_id` entries inside that bundle.
- `i18n-ops review validate` mechanically checks schema compliance, `corpus_snapshot_hash` alignment, exact `(evidence_id, doc_id, doc_version, excerpt_hash)` existence in `index.sqlite`, EN-only authorization prohibition, and verbatim UI text match.
- `i18n-ops review consistency` checks cross-menu drift **per `(concept_key, domain_tag)`**: same-domain terminology drift is flagged as `conflict`, while intentional cross-domain polysemy is reported separately as `advisory_polysemy`. Unconfirmed AI suggestions are never auto-written back into the authoritative glossary.

## 4. 6-Column Color-Coded Excel Deliverable

`i18n-ops export` produces one worksheet per deliverable feature menu in menu-map order with **6 fixed columns**:

| Col | Header | Content Rule |
|---|---|---|
| A | `页面层级` | Full UI hierarchy path (`菜单 > 页面状态 > 区域 > 控件`) |
| B | `系统英文` | Verbatim observed English UI text (explicit text cell, formula-safe) |
| C | `系统中文` | Verbatim observed target-language UI text (empty string if missing) |
| D | `AI 建议中文` | Suggested target term (empty string when `保留` or uncertain/insufficient evidence) |
| E | `比对结论` | Concise verdict enum with distinct cell fill & font color (see table below) |
| F | `修改理由与业务依据` | **Mandatory non-empty whenever `比对结论 != 保留`**. Write business-first rationale explaining domain meaning, risk, and brief provenance tag (e.g., `[CN-FX-2025 §1.1]`). Optional/blank for `保留`. |

### Verdict Statuses & Cell Fill Colors (`VERDICT_STYLES`)

| `比对结论` | Fill Hex | Font Hex | When to Use |
|---|---|---|---|
| `保留` | `#E8F5E9` | `#1B5E20` | Current translation is accurate and consistent in context |
| `建议修改` | `#FFF8E1` | `#8D6E00` | Mistranslation, non-standard term, or same-domain drift with in-scope bilingual evidence |
| `缺译` | `#FFEBEE` | `#B71C1C` | Missing target text or exposed raw i18n key |
| `来源冲突` | `#FFF3E0` | `#E65100` | Multiple in-scope authoritative sources disagree; present both options for business sign-off |
| `单语限制` | `#ECEFF1` | `#37474F` | Confirmed single-language embedded iframe or third-party component |
| `待业务确认` | `#E3F2FD` | `#0D47A1` | Insufficient bilingual evidence (e.g., EN-only definition or custom internal concept) |
| `待核对` | `#F3E5F5` | `#4A148C` | Unpaired bilingual control, single-side observation, or blocked inspection |

### Formula Safety & Mandatory Read-Back

- All cells are written as explicit text (`data_type = "s"`, `number_format = "@"`) so formula-like UI labels (`=SpotRate + Margin`, `+BP Spread`, `@user`) are preserved verbatim without creating `<f>` formula nodes or external links.
- Pass `--with-summary` to append `_Summary` and `_Consistency` tabs after feature menu sheets.
- Full structured provenance stays in sibling `review.json` and `delivery-manifest.json`. Every export runs `verify_delivery` automatically to check workbook SHA-256, sheet order, headers, verbatim text, row hashes, verdict fill colors, and zero formula cells.
