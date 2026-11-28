# Evidence, coverage ledger, and crash-safe checkpoints

## 1. Source of truth and handoff views

All runtime files live inside the external workspace (`--workspace <dir>`):

```text
<workspace>/
  run.json                     # Machine source of truth for scope, phase, snapshot hashes & task units
  task_plan.md                 # Human-readable phase checklist (derived handoff view)
  findings.md                  # Key observations, blockers & open business questions
  progress.md                  # Latest checkpoint summary
  records/
    menu-map.json              # Authoritative menu tree & coverage status
    pages/<page_id>.json       # Authoritative control-level EN/ZH UI observations
    packets.json               # Controlled retrieval bundles prepared for review
    review.json                # Structured terminology decisions & consistency findings
  corpus/
    raw/                       # User-supplied PDF/HTML/XLSX/CSV glossaries & standards (BYOG)
    web_cache/                 # Locally cached L2 web evidence files added via `corpus add-web`
    manifest.json              # Document metadata, domain tags, region, normative level & SHA-256
    authority_policy.json      # Config-driven applicability, precedence & web domain tiers
    normalized/                # Deterministic parsed JSONL artifacts
    index.sqlite               # Rebuildable SQLite + FTS5 + CJK bigram index
  deliverables/
    audit.xlsx                 # 6-column color-coded Excel deliverable (projection only)
    delivery-manifest.json     # Sheet mapping, row hashes, workbook SHA-256 & read-back status
```

- **Machine truth**: `run.json` and `records/*.json` are authoritative. Markdown files (`task_plan.md`, `findings.md`, `progress.md`) exist for human reading and session handoff. Never rely on Markdown checkboxes alone to determine completion.
- **Derived outputs**: `corpus/index.sqlite` can be rebuilt from `corpus/normalized/`, and `deliverables/audit.xlsx` is a read-only export of `records/review.json`.

## 2. Granularity, observation states, and coverage states

- **Minimum coverage unit**: `menu_id × page_id × state_id × language`. Control observations attach beneath each state with a stable `item_id` or `control_id` decoupled from display text or DOM row index.
- **Observation status (`en_status` / `zh_status`)**:
  - `observed`: Verbatim text observed on screen (must be non-empty).
  - `missing_or_raw_key`: Target text is missing or exposes a raw i18n key (for example, `fx.spot.settlement_instruction`); record the raw key verbatim.
  - `not_observed`: Not yet inspected or unreachable.
  - `single_language_embedded`: Confirmed single-language embedded iframe or component.
  - `pairing_pending`: Bilingual pairing across English and the target locale is ambiguous and awaits verification.
- **Coverage status (`coverage_status`)**:
  - `registered_only`: Entry discovered in navigation or DOM, not yet opened.
  - `fully_inspected`: All safe states and static options verified to the end.
  - `partially_inspected`: Some controls or options inspected; remaining gaps documented with blocker reasons.
  - `blocked`: Cannot be safely inspected (for example, permission limit, auto-save risk without isolation, reset failure, or 0 items matched by built-in selectors).
- **Strict denominator rule**: Never count `blocked` or `skipped` units as completed coverage.

## 3. Incremental per-module persistence and safe resume

- **Write and append after every module**: Do not hold dozens of menus in conversation context until the end of the run. As soon as you finish one feature module (menu):
  1. Write and validate its `records/pages/<page_id>.json` and update its entry in `records/menu-map.json`.
  2. Prepare packets, review that module, validate citations, and merge its decisions into `records/review.json`.
  3. Re-run `lingual-audit export` and `lingual-audit verify` so `deliverables/audit.xlsx` immediately includes that module's sheet; each subsequent module appends into the same ledger and workbook.
- **Atomic writes**: Every JSON and SQLite write uses same-directory temporary file staging, `fsync`, and atomic `os.replace()` under a single-writer convention (which matters on Windows and WSL DrvFS mounts).
- **Safe resume**: Run `lingual-audit run resume --workspace <dir>` after any interruption:
  1. Any task unit left in `running` state is reset to `pending` with a reminder to re-check clean UI state. Never replay interrupted synthetic input actions automatically.
  2. Corpus manifest and snapshot hashes are checked. If reference documents or policies changed, downstream reviews are flagged for re-validation so stale conclusions cannot stay in place silently.
