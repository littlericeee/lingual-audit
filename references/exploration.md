# Exploration and bilingual UI collection

This reference defines the built-in discovery protocol for `lingual-audit`. It covers route and state discovery as well as control-level collection on complex web systems, without requiring any vendored third-party skills.

## 1. Browser tool selection and session hygiene

- **Choose the right browser tool**: For real-world apps with custom DOMs, capture UI strings with your host's Chrome or Playwright MCP browser tools and write validated JSON inventories per module. The built-in CLI runner (`lingual-audit explore`) is **experimental** and relies on standard semantic attributes (`[data-menu]`, `[data-i18n]`, `placeholder`); if it observes 0 translatable items on a page, it marks that page and task unit as `blocked` rather than `fully_inspected`. Use `lingual-audit explore` when you want deterministic batch collection on annotated pages or isolated `thorough` validation probing (requires `playwright>=1.48`).
- **Let the user sign in**: Keep authentication inside the user's browser session. Never read, copy, print, or commit `.env*`, cookies, credentials, or browser `storage-state` files.
- **Check the rendered page, not just the URL**: A URL change or language-toggle label does not prove that login or a language switch succeeded. Always check `html[lang]`, the visible page title, and menu text.
- **Wait for loading states to settle**: Before extracting UI strings, wait until spinners, skeleton placeholders, and initial network requests finish so untranslated fallback keys or temporary loading text are not recorded.

## 2. Navigation and menu map discovery

Build `records/menu-map.json` from the interface visible to the current role:

1. **Primary navigation**: Expand every top-level menu group and leaf page, recording `menu_id`, `parent_menu_id`, `is_group`, `hierarchy_en`, `hierarchy_zh`, `route_or_entry`, and user-defined `domain_tag`.
2. **Global non-sidebar surfaces**: Inspect and register global chrome that sidebar-only crawls often miss: top navigation bars, user profile menus, language switchers, settings and preferences, help or about modals, and notification drawers.
3. **Embedded frames and modals**: When a menu opens an embedded `iframe` or layered dialog, record `iframe_context` and collect only from the active interactive layer. Mark single-language embedded frames explicitly as `single_language_embedded`.

## 3. Control-level coverage inside each page state

Inspect horizontal, vertical, and nested scroll regions across distinct page states (list, search bar, detail, create or edit form, tabs, accordions, modals):

| Control Category | Required Read-Only Action | What to Record |
|---|---|---|
| Search / text inputs | Focus without submitting; check placeholder, clear icon, helper text | Label, placeholder, tooltip, static hint (never typed values) |
| Dropdowns, comboboxes, multi-select, date pickers | Open and scroll to the explicit end of static options | Static UI enum options in order, group headers, disabled state; exclude concrete dates and dynamic entity lists |
| Radios, checkboxes, toggles | Inspect all visible options; toggle only when confirmed to affect local display/filtering | Group label, option labels, disabled state, revealed sub-controls |
| Modal / cascading pickers | Open modal; inspect search headers, tabs, buttons, and safe static branches | Modal title, static column headers, fixed tabs/options; exclude dynamic business rows |
| Tabs, accordions, safe forms | Enter each safely accessible state | Section headers, field labels, required/read-only hints, visible action buttons |

- **Exclude dynamic business data**: Record only translatable UI chrome and confirmed static enum options. Exclude table rows, customer or account names, order numbers, live balances, and dynamic lookup candidates.
- **Pair English and target strings safely**: Match bilingual items by stable attributes (`data-i18n`, `id`, `name`, `for`, field code) or reproducible control structure and state, never by blind DOM traversal order. Mark uncertain pairs as `pairing_pending`.

## 4. Exploration depths and `thorough` network isolation

| Depth | Permitted Scope | Coverage Reporting Requirement |
|---|---|---|
| `quick` | Declared sample menus, default page states, and primary visible controls | Explicitly state sampled subset; never report as full-system completion |
| `standard` | Full visible menu tree, global chrome, tabs, accordions, modals, safe static options, and safe local cascading filters | Report per-state coverage, blocked entries, and unconfirmed option lists |
| `thorough` | Everything in `standard`, plus synthetic test-data probing on initially empty inputs to surface client-side validation messages and inline error states | Report probe results, reset verification, and any unit skipped for safety |

### Guardrails for `thorough` synthetic input probing

Skipping the Submit button is not a safety guarantee. Complex web forms frequently fire `POST` or `PUT` auto-save requests on `input`, `change`, `blur`, or debounced timers. Follow these rules strictly:

1. **Require network isolation**: Run `thorough` synthetic probes inside a dedicated Playwright context (`playwright>=1.48`) with `service_workers="block"` and outbound HTTP and WebSocket blockers active for the entire probe session. Never call `unroute`; destroy the probe context immediately after reset verification.
2. **Skip automatically when isolation is unavailable**: If the host environment cannot intercept network traffic (for example, an extension-only browser or Playwright < 1.48 without `route_web_socket`), skip synthetic input probing and mark those states as `skipped` or `blocked` with a reason.
3. **Block mutating actions**: Never click save, submit, execute, approve, delete, import, or export buttons, and never send `Enter` keys that could submit a form.
4. **Probe initially empty inputs only**: Test only empty, clear-purpose inputs using benign synthetic strings (such as `123!`). Never overwrite pre-populated business data.
5. **Verify reset and filter probe noise**: Clear or reset inputs while still under network isolation and confirm the input is empty. If reset fails, destroy the browser context immediately and mark the unit `blocked`. Filter out network-abort error banners and echoed test input strings so they never enter static UI records.
