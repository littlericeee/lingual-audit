# enterprise-i18n-ops

An enterprise-grade bilingual UI inventory, terminology standardization, and tiered hybrid RAG audit workflow for complex B2B web systems.

> **Status:** Initializing architecture & core contracts

## Architecture Overview (Planned)

1. **Read-Only Bilingual UI Discovery**: Menu-by-menu, control-level bilingual inventory with file-based checkpointing (inspired by `app-docs` and `planning-with-files`, with a self-contained fallback so no third-party skill vendoring is required).
2. **Tiered Hybrid Retrieval & Arbitration (RAG)**:
   - **L1 Deterministic Glossary**: Exact & alias matching over structured domain glossaries.
   - **L2 Context-Aware Hybrid Retrieval**: BM25 + vector retrieval + reranking using menu path, control type, and neighboring fields as disambiguation context.
   - **L3 Authority Arbitration & Consistency Audit**: Configurable authority precedence, traceable citations (publisher, document, clause/page), and cross-menu terminology consistency checks.
3. **Traceable Delivery & Evaluation**: Structured per-menu `.xlsx` audit workbook with row-level provenance (`records/review.json`) and a golden-set evaluation harness comparing baseline vs. RAG-assisted review.

## Companion Skills (Optional, Not Vendored)

This skill is self-contained out of the box and does **not** vendor third-party skill packages. When available in the host agent environment, it optionally pairs with:

- `app-docs` — route/menu discovery and UI state inspection conventions
- `planning-with-files` — persistent `task_plan.md` / `findings.md` / `progress.md` session recovery
- `playwright` — browser automation driver for read-only bilingual collection
