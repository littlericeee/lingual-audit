# Exploration & Bilingual UI Collection

This reference defines the native, self-contained discovery protocol for `enterprise-i18n-ops`. It absorbs universal route/state discovery mechanics and combines them with B2B control-level rigor—requiring zero vendored third-party skills.

## 1. Browser Tool Detection & Session Hygiene

- **Auto-detect capability**: Prefer this repository's built-in Playwright runner (`i18n-ops explore`) when `thorough` synthetic input probing or deterministic batch collection is needed. For `quick` or `standard` read-only browsing, host-provided Chrome/Playwright MCP tools may also be used.
- **User-managed authentication**: Let the user sign in inside their browser session. Never read, copy, print, or commit `.env*`, cookies, credentials, or browser `storage-state` files.
- **Verify actual rendered state**: Never treat a URL change or language-toggle button label as proof of login or language switch. Always verify the rendered document language (`html[lang]`), visible page title, and menu text.
- **Anti-skeleton / stability gate**: Before extracting UI text, wait until loading spinners, skeleton placeholders, and pending initial fetches settle so untranslated fallback keys or transient loading labels are not misrecorded.

## 2. Global Navigation & Menu Map Discovery

Build `records/menu-map.json` from the live interface visible to the current role:

1. **Primary navigation**: Expand every top-level menu group and leaf page, recording `menu_id`, `parent_menu_id`, `is_group`, `hierarchy_en`, `hierarchy_zh`, `route_or_entry`, and user-defined `domain_tag`.
2. **Global non-sidebar chrome**: Explicitly inspect and register global surfaces often missed in sidebar-only crawls—top navigation bar, user profile & language switcher, system settings/preferences, help/about modals, and notification drawers.
3. **Nested frames & modals**: When a leaf menu opens an embedded `iframe` or layered dialog, record `iframe_context` and collect only from the active, interactive layer. Mark single-language embedded frames explicitly as `single_language_embedded`.

## 3. Control-Level Coverage Inside Each Page State

Inspect horizontal, vertical, and nested scroll regions across distinct page states (list, search bar, detail, create/edit form, tabs, accordions, modals):

| Control Category | Required Read-Only Action | What to Record |
|---|---|---|
| Search / text inputs | Focus without submitting; check placeholder, clear icon, helper text | Label, placeholder, tooltip, static hint (never typed values) |
| Dropdowns, comboboxes, multi-select, date pickers | Open and scroll to the explicit end of static options | Static UI enum options in order, group headers, disabled state; exclude concrete dates and dynamic entity lists |
| Radios, checkboxes, toggles | Inspect all visible options; toggle only when confirmed to affect local display/filtering | Group label, option labels, disabled state, revealed sub-controls |
| Modal / cascading pickers | Open modal; inspect search headers, tabs, buttons, and safe static branches | Modal title, static column headers, fixed tabs/options; exclude dynamic business rows |
| Tabs, accordions, safe forms | Enter each safely accessible state | Section headers, field labels, required/read-only hints, visible action buttons |

- **Static UI vs. Dynamic Data Exclusion**: Record only translatable UI chrome and confirmed static enum options. Exclude business table rows, customer/account/order names, live balances, and dynamic lookup candidates.
- **Bilingual Pairing**: Pair English and target-locale items by stable attribute (`data-i18n`, `id`, `name`, `for`, field code) or reproducible control structure & state—never by blind DOM traversal order. Mark uncertain pairs as `pairing_pending`.

## 4. Exploration Depth Modes & `thorough` Network Isolation

| Depth | Permitted Scope | Coverage Reporting Requirement |
|---|---|---|
| `quick` | Declared sample menus, default page states, and primary visible controls | Explicitly state sampled subset; never report as full-system completion |
| `standard` | Full visible menu tree, global chrome, tabs, accordions, modals, safe static options, and safe local cascading filters | Report per-state coverage, blocked entries, and unconfirmed option lists |
| `thorough` | Everything in `standard`, plus **synthetic test-data probing** on initially empty inputs to surface client-side validation messages and inline error states | Report probe results, reset verification, and any unit skipped for safety |

### Hard Guardrails for `thorough` Synthetic Input Probing

Never rely on "not clicking Submit" as a safety guarantee—B2B forms frequently fire implicit `POST`/`PUT` auto-save requests on `input`, `change`, or `blur`. Therefore:

1. **Mandatory Network Isolation**: `thorough` synthetic probing requires the Playwright runner to launch with `service_workers="block"` and install outbound HTTP + WebSocket request blockers **before** typing synthetic test strings, keeping isolation active until form reset is verified.
2. **Automatic Safe Skip**: If the host environment cannot enforce network isolation (e.g., an extension-only browser without request interception), **skip synthetic input probing** and mark those states as `skipped` / `blocked` with reason.
3. **Strict Action Denylist**: Never click save, submit, execute, approve, delete, import, or export buttons, and never send implicit-submit `Enter` keys.
4. **Initially Empty Inputs Only**: Probe only empty, clear-purpose inputs using benign synthetic strings (e.g. `123!`). Never overwrite pre-populated business data.
5. **Reset Verification & Signal Filtering**: Clear/reset inputs while still under network isolation and verify the input is empty. If reset fails, destroy the browser context immediately and mark the unit `blocked`. Filter out network-abort error banners and echoed test input strings so they never pollute static UI records.
