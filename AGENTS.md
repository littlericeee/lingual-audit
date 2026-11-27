# Repository Engineering & Safety Guidelines

## Security & Sanitization Baseline
- Never read, print, or echo `.env`, `.env.local`, `.env.production`, credential files, cookies, or browser profile/storage-state files. Automated tests may only use committed `.env.test` files, sanitized fixtures, or public demo environments.
- Never include real company names, internal product names, intranet IP addresses/URLs, proprietary glossaries, real user accounts, or unsanitized business data anywhere in code, documentation, test fixtures, or examples.
- Keep target-system exploration strictly read-only by default: collect only translatable UI labels, prompts, and confirmed static options; exclude dynamic business rows, candidate entity values, user inputs, and credentials.

## Contracts & Deterministic Checks
- Shared contracts (JSON Schemas and CLI specifications) are the single source of truth shared between the Agent Skill instructions (`SKILL.md`, `references/`), the corpus ingestion/RAG retrieval engine, and Excel delivery scripts.
- Rely on deterministic tooling first: catch structural, schema, citation-mapping, encoding, and sanitization issues via type checks, linters, unit tests, and golden-set evaluation suites rather than manual memory.
- Keep changes focused on the stated goal; avoid unrequested features or obsolete compatibility layers.

## Completeness & Verification
- Before modifying a field, status enum, authority tier, or CLI flag, search across the entire repository with `git grep` to locate every reference in code, schemas, documentation, and tests, and update them together.
- After completing changes, search for old terms or flags to verify no stale references remain.
- **Scope priority for isolated workers**: When operating inside a file-allowlisted isolated task (such as a dedicated worker worktree), the explicit file allowlist takes precedence over repository-wide edits. Do not modify files outside the allowlist; list any needed follow-up changes in the final report for the coordinator.

## Factuality & Citation Integrity
- Every technical claim, evaluation metric, and terminology recommendation must be grounded in actual repository code, verified test output, or traceable reference documents (organization, document title, clause/page). Explicitly mark any unverified inference with `【推测】`.
