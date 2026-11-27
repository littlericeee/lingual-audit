# Evidence, Coverage Ledger, and Crash-Safe Checkpoints

## 1. Source of Truth vs. Handoff Views

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
    manifest.json              # Document metadata, domain tags, region, normative level & SHA-256
    authority_policy.json      # Config-driven applicability & precedence policy
    normalized/                # Deterministic parsed JSONL artifacts
    index.sqlite               # Rebuildable SQLite + FTS5 + CJK bigram index
  deliverables/
    audit.xlsx                 # 6-column color-coded Excel deliverable (projection only)
    delivery-manifest.json     # Sheet mapping, row hashes, workbook SHA-256 & read-back status
```

- **Machine Truth**: `run.json` and `records/*.json` are authoritative. Markdown files (`task_plan.md`, `findings.md`, `progress.md`) exist for human reading and session handoff—never use Markdown checkboxes alone to determine machine completion.
- **Derived Projections**: `corpus/index.sqlite` is rebuildable from `corpus/normalized/`; `deliverables/audit.xlsx` is a read-only projection of `records/review.json`.

## 2. Granularity, Observation States & Coverage States

- **Minimum Coverage Unit**: `menu_id × page_id × state_id × language`. Control observations attach beneath each state with stable `item_id` / `control_id` decoupled from display text or DOM row index.
- **Observation Status (`en_status` / `zh_status`)**:
  - `observed`: Verbatim text observed on screen (must be non-empty).
  - `missing_or_raw_key`: Target text is missing or exposes a raw i18n key (e.g., `fx.spot.settlement_instruction`); record the raw key verbatim.
  - `not_observed`: Not yet inspected or unreachable.
  - `single_language_embedded`: Confirmed single-language embedded iframe/component.
  - `pairing_pending`: Bilingual pairing across EN and target locale is ambiguous and awaits verification.
- **Coverage Status (`coverage_status`)**:
  - `registered_only`: Entry discovered in navigation/DOM, not yet opened.
  - `fully_inspected`: All safe states and static options verified to the end.
  - `partially_inspected`: Some controls/options inspected; remaining gaps documented with blocker reasons.
  - `blocked`: Cannot safely inspect (e.g., permission limit, auto-save risk without isolation, reset failure).
- **Strict Denominator Rule**: `blocked` and `skipped` units are never counted as completed coverage.

## 3. Atomic Checkpointing & Safe Resume

- Every JSON and SQLite update uses **same-directory temporary file staging + `fsync` + atomic `os.replace()`** under a single-writer convention (important on Windows/WSL DrvFS mounts).
- Run `i18n-ops run resume --workspace <dir>` after any interruption:
  1. Any task unit left in `running` state is safely re-queued to `pending` with a reminder to re-verify clean UI state. **Never replay interrupted synthetic input actions automatically.**
  2. Corpus manifest and snapshot hashes are checked; if reference documents or policies changed, downstream reviews are flagged for re-validation so stale conclusions cannot silently persist.
