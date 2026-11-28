# BYOG + web hybrid RAG review and 6-column Excel delivery

## 1. Setting up a Bring-Your-Own-Glossary (BYOG) corpus and web evidence cache

`lingual-audit` is industry-agnostic and starts with an empty knowledge base slot. To audit a web system in any domain (finance, healthcare/EHR, manufacturing/MES, legal, supply chain, SaaS):

1. Drop your reference documents (`PDF`, `HTML`, `XLSX`, `CSV`) into `<workspace>/corpus/raw/`.
2. Register each document in `<workspace>/corpus/manifest.json` with open `domain_tags` (for example, `clinical_trial`, `warehouse_wms`, `fx`), `region_scope` (`CN_MAINLAND`, `HK`, `US`, `EU`, `GLOBAL`), `target_locale` (`zh-CN`, `zh-HK`), `normative_level`, and `language_scope` (`bilingual_en_zh` | `zh_only` | `en_only`).
3. Edit `<workspace>/corpus/authority_policy.json` to set your industry's `normative_precedence`, `region_fallback_order`, `domain_hierarchy`, `web_domain_tiers`, `web_default_normative_level`, and `conflict_rules`.
4. Whenever the host agent searches the web to supplement missing terms, persist each verified finding locally with `lingual-audit corpus add-web --manifest <ws>/corpus/manifest.json --url <URL> --title "<Title>" --excerpt "<Verbatim excerpt>" --en-term "<EN>" --zh-term "<ZH>" --policy <ws>/corpus/authority_policy.json`. This saves a deterministic cache file under `corpus/web_cache/` and appends it to `manifest.json` with `source_url`, `fetched_at`, and `file_sha256`.
5. Run `lingual-audit corpus ingest` and `lingual-audit corpus index`.

If `documents` is empty, ingestion and indexing still finish cleanly, and retrieval returns `candidates: []` with `conflict_flags.no_authoritative_hit = true`. The review will not crash or invent citations.

## 2. Three-tier evidence hierarchy and applicability-first retrieval

Review decisions combine three evidence tiers in strict priority order:

1. **L1 Local BYOG corpus (highest authority)**: Your own glossaries, standards, and field dictionaries in `corpus/raw/`.
2. **L2 Web-sourced evidence (`corpus add-web`)**: Used when L1 has no hit or needs disambiguation. Cached locally in `corpus/web_cache/`, ranked by `web_domain_tiers` (falling back to `web_default_normative_level`), and checked by the exact same `index.sqlite` and `packets.json` citation verifier as L1.
3. **L3 LLM language judgment (fallback)**: Used for spotting obvious mistranslations, awkward phrasing, raw keys, and making a clear `保留` vs `建议修改` call when neither L1 nor L2 provides an external bilingual standard. Always note `[LLM 语义判断]` in `修改理由与业务依据` so reviewers can distinguish language judgment from cited standards.

Retrieval (`lingual-audit retrieve` / `lingual-audit review prepare`) combines up to three channels:

1. **Exact and alias glossary channel (`exact_glossary` / `alias_glossary`)**: NFKC-normalized exact matching over terms, aliases, and field codes. This resolves 1 to 3 character acronyms (such as `FX`, `IRS`, `BIC`) reliably and keeps all polysemous candidates.
2. **Context-aware FTS5 and CJK bigram channel (`fts_bm25` / `cjk_bigram`)**: Queries SQLite FTS5 using `en_text`, `hierarchy_path`, `control_type`, `sibling_labels`, and `domain_tag`. Existing target-language UI text is used only as a weak secondary tie-breaker so mistranslations do not skew retrieval.
3. **Optional dense vector and RRF channel (`dense_vector`)**: Fuses lexical and semantic ranks via Reciprocal Rank Fusion when embeddings are present, and falls back to `glossary_fts` when they are absent.

### Screening before ranking

Sources are screened for applicability (`domain` + `domain_hierarchy`, `region_scope` + `region_fallback_order`, `target_locale`, `language_scope`) before comparing `normative_precedence`:

- `in_scope`: Matches target domain, region or fallback, and bilingual support.
- `regional_variant`: Matches domain with bilingual support, but belongs to another region or locale. Preserved as a valid regional variant instead of being overwritten silently.
- `en_definition_only`: English-only standard or definition (`support_type == "concept_definition_only"`). Explains concept meaning only and cannot authorize a `建议修改` Chinese translation.
- `cross_domain_reference`: Matches region but belongs to a different domain, preventing cross-domain polysemy collisions.
- `same_scope_conflict`: Triggered when two or more distinct target translations appear among `in_scope` bilingual sources. Surface both citations and mark the item `来源冲突`.

## 3. Packet-bound citation verification and consistency checks

- The host LLM receives each item's `ui_item` and `evidence_bundle` (`records/packets.json`) and may cite only `evidence_id` entries inside that item's bundle.
- `lingual-audit review validate` (automatically reading `<records-dir>/packets.json` or an explicit `--packets` path) mechanically checks schema compliance, `corpus_snapshot_hash` alignment, exact `(evidence_id, doc_id, doc_version, excerpt_hash)` existence in both `index.sqlite` and that item's `evidence_bundle`, non-empty bilingual/target-language authorization for `建议修改`, the English-only authorization ban, and verbatim UI text match.
- `lingual-audit review consistency` checks cross-menu drift per `(concept_key, domain_tag)`: same-domain drift is flagged as `conflict`, while intentional cross-domain polysemy is reported separately as `advisory_polysemy`. Unconfirmed AI suggestions are never written back into the authoritative glossary automatically.

## 4. 6-column color-coded Excel deliverable

`lingual-audit export` produces one worksheet per deliverable feature menu in menu-map order with 6 fixed columns. Export incrementally after finishing each module so every completed module is saved to `audit.xlsx` immediately and subsequent modules append into the same workbook:

| Col | Header | Content Rule |
|---|---|---|
| A | `页面层级` | Full UI hierarchy path (`菜单 > 页面状态 > 区域 > 控件`) |
| B | `系统英文` | Verbatim observed English UI text (explicit text cell, formula-safe) |
| C | `系统中文` | Verbatim observed target-language UI text (empty string if missing) |
| D | `AI 建议中文` | Suggested target term (empty string when `保留` or unverified) |
| E | `比对结论` | Concise verdict enum with distinct cell fill & font color (see table below) |
| F | `修改理由与业务依据` | Required non-empty whenever `比对结论 != 保留`. Write a business-first rationale explaining domain meaning, risk, and brief provenance tag (for example, `[CN-FX-2025 §1.1]` or `[Web:w3.org]`). Optional or blank for `保留`. |

### Verdict statuses and cell fill colors (`VERDICT_STYLES`)

`待核对` is reserved strictly for items whose bilingual UI text was not actually verified. Once both languages are verified on screen, do not leave items in a middle state: choose `保留` or `建议修改` (aside from `缺译`, `单语限制`, and `来源冲突`).

| `比对结论` | Fill Hex | Font Hex | When to Use |
|---|---|---|---|
| `保留` | `#E8F5E9` | `#1B5E20` | Bilingual text verified; current translation is accurate and consistent in context |
| `建议修改` | `#FFF8E1` | `#8D6E00` | Bilingual text verified; mistranslation, non-standard term, or same-domain drift |
| `缺译` | `#FFEBEE` | `#B71C1C` | Missing target text or exposed raw i18n key |
| `来源冲突` | `#FFF3E0` | `#E65100` | Multiple in-scope authoritative sources disagree; present both options for business sign-off |
| `单语限制` | `#ECEFF1` | `#37474F` | Confirmed single-language embedded iframe or third-party component |
| `待核对` | `#F3E5F5` | `#4A148C` | Bilingual UI text was genuinely not verified (unpaired control, single-side observation, or blocked inspection) |

### Formula safety and automatic read-back

- All cells are written as explicit text (`data_type = "s"`, `number_format = "@"`) so formula-like UI labels (`=SpotRate + Margin`, `+BP Spread`, `@user`) stay verbatim without creating `<f>` formula nodes or external links.
- Pass `--with-summary` to append `_Summary` and `_Consistency` tabs after feature menu sheets.
- Full structured provenance stays in sibling `review.json` and `delivery-manifest.json`. Every export runs `verify_delivery` automatically to check workbook SHA-256, sheet order, headers, verbatim text, row hashes, verdict fill colors, and zero formula cells.
