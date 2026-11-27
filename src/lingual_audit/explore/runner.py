"""Synchronous Playwright runner for policy-constrained bilingual discovery."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lingual_audit.contracts import (
    CONTRACT_VERSION,
    ContractValidationError,
    sha256_text,
    validate_payload,
)
from lingual_audit.explore.policy import DEPTHS, ExplorationPolicy
from lingual_audit.state import (
    atomic_write_json,
    atomic_write_text,
    init_workspace,
    load_run_state,
    save_run_state,
    utc_now_iso,
)

_LOADING_SELECTOR = ".loading:visible, .spinner:visible, .skeleton:visible, [aria-busy='true']:visible"
_VALIDATION_SELECTOR = (
    ".err:visible, .error:visible, [role='alert']:visible, "
    "[aria-live='assertive']:visible, [data-validation]:visible"
)


def _normalized_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _by_id(page: Any, elem_id: str) -> Any:
    return page.locator(f"[id={json.dumps(elem_id)}]")


def _wait_for_stability(page: Any) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
    except PlaywrightTimeoutError:
        # A quiet network is a stability hint; spinner absence is checked next.
        page.wait_for_timeout(100)
    try:
        page.locator(_LOADING_SELECTOR).first.wait_for(state="hidden", timeout=5_000)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(100)


def _visible_snapshot(page: Any) -> dict[str, dict[str, Any]]:
    rows = page.locator("[data-i18n]").evaluate_all(
        """elements => elements.filter(el => {
          const s = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
        }).map(el => ({
          key: el.getAttribute('data-i18n'),
          text: (el.textContent || '').trim(),
          tag: el.tagName.toLowerCase(),
          id: el.id || null,
          name: el.getAttribute('name'),
          forId: el.getAttribute('for'),
          disabled: Boolean(el.disabled),
          dynamic: Boolean(el.closest('[data-dynamic], tbody tr, [role=row]'))
        }))"""
    )
    placeholders = page.locator("input[placeholder], textarea[placeholder]").evaluate_all(
        """elements => elements.filter(el => {
          const s = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
        }).map(el => ({
          key: `placeholder:${el.id || el.name}`,
          text: el.placeholder,
          tag: 'placeholder',
          id: el.id || null,
          name: el.name || null,
          forId: null,
          disabled: Boolean(el.disabled),
          dynamic: false
        }))"""
    )
    rows.extend(placeholders)
    return {row["key"]: row for row in rows if row.get("key")}


def _switch_language(page: Any, language: str) -> None:
    expected = "zh" if language == "zh" else "en"
    current = (page.locator("html").get_attribute("lang") or "").lower()
    if current.startswith(expected):
        return
    before = page.locator("body").inner_text()
    toggle = page.locator("#lang-toggle, [data-language-toggle], [aria-label*='language' i]").first
    if toggle.count() == 0:
        raise ValueError(f"Could not find a rendered language control for {language}")
    toggle.click()
    page.wait_for_function(
        "expected => document.documentElement.lang.toLowerCase().startsWith(expected)",
        arg=expected,
    )
    page.wait_for_function("before => document.body.innerText !== before", arg=before)


def _control_type(row: dict[str, Any]) -> tuple[str, str]:
    tag = row["tag"]
    key = row["key"]
    if key.startswith("nav."):
        return ("menu_item", "menu")
    if tag == "h1":
        return ("page_title", "header")
    if tag in {"h2", "h3", "h4"}:
        return ("section_header", "header")
    if tag == "label":
        return ("label", "label")
    if tag == "button":
        return ("button", "button")
    if tag == "option":
        return ("dropdown_option", "option")
    if tag == "placeholder":
        return ("input_placeholder", "placeholder")
    if "err" in key or "validation" in key:
        return ("validation_message", "validation")
    return ("tooltip_or_hint", "other")


def _domain_for_key(key: str) -> str:
    prefix = key.split(".", 1)[0].lower()
    return prefix if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", prefix) else "general_ui"


def _make_item(
    index: int,
    key: str,
    en: dict[str, Any] | None,
    zh: dict[str, Any] | None,
    *,
    state_id: str,
    hierarchy: str,
    synthetic_probe: bool = False,
) -> dict[str, Any]:
    basis = en or zh or {"tag": "div", "disabled": False}
    control_type, text_kind = _control_type({**basis, "key": key})
    en_text = _normalized_text(en.get("text") if en else None) or None
    zh_text = _normalized_text(zh.get("text") if zh else None) or None
    return {
        "item_id": f"UI-{index:06d}",
        "control_id": f"CTL-{index:06d}",
        "state_id": state_id,
        "hierarchy_path": f"{hierarchy} > {en_text or zh_text or key}",
        "control_type": control_type,
        "text_kind": text_kind,
        "en_status": "observed" if en_text else "not_observed",
        "zh_status": (
            "missing_or_raw_key" if zh_text == key else "observed" if zh_text else "not_observed"
        ),
        "en_text": en_text,
        "zh_text": zh_text,
        "i18n_key_or_code": key,
        "pairing_method": "stable_key_or_code",
        "disabled": bool(basis.get("disabled")),
        "synthetic_probe": synthetic_probe,
        "notes": "Captured with outbound HTTP and WebSocket blocking active." if synthetic_probe else None,
    }


def _validation_snapshot(page: Any, policy: ExplorationPolicy) -> dict[str, str]:
    rows = page.locator(_VALIDATION_SELECTOR).evaluate_all(
        "elements => elements.map(el => ({key: el.getAttribute('data-i18n') || el.id, text: el.textContent || ''}))"
    )
    return {
        str(row["key"]): _normalized_text(row["text"])
        for row in rows
        if row.get("key") and policy.keep_observation(str(row.get("text", "")))
    }


def _probe_inputs(
    page: Any, policy: ExplorationPolicy
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    results: dict[str, Any] = {}
    messages: dict[str, dict[str, str]] = {"en": {}, "zh": {}}
    candidates = page.locator("input").evaluate_all(
        """elements => elements.map(el => ({
          id: el.id, type: el.type, value: el.value, name: el.name,
          placeholder: el.placeholder, disabled: el.disabled, readOnly: el.readOnly,
          label: (el.labels && el.labels[0] && el.labels[0].textContent) || ''
        }))"""
    )

    for candidate in candidates:
        purpose = " ".join(
            str(candidate.get(key) or "") for key in ("label", "placeholder", "name", "id")
        )
        if not candidate.get("id") or not policy.is_probe_candidate(
            input_type=str(candidate.get("type", "")),
            value=str(candidate.get("value", "")),
            purpose=purpose,
            disabled=bool(candidate.get("disabled")),
            readonly=bool(candidate.get("readOnly")),
        ):
            continue
        input_id = str(candidate["id"])
        locator = _by_id(page, input_id)
        for language in ("en", "zh"):
            _switch_language(page, language)
            try:
                page.wait_for_load_state("networkidle", timeout=3_000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(100)

            locator.fill("123!")
            locator.blur()
            page.wait_for_timeout(50)
            messages[language].update(_validation_snapshot(page, policy))
            locator.fill("")
            locator.blur()
            page.wait_for_timeout(50)
            reset_verified = locator.input_value() == "" and not _validation_snapshot(page, policy)
            results.setdefault(input_id, {"languages": [], "reset_verified": True})
            results[input_id]["languages"].append(language)
            results[input_id]["reset_verified"] &= reset_verified
            if not reset_verified:
                raise RuntimeError(f"Synthetic probe reset verification failed for #{input_id}")
    return results, messages


def _validate(kind: str, payload: dict[str, Any]) -> None:
    errors = validate_payload(kind, payload)
    if errors:
        raise ContractValidationError(errors)


def explore_target(
    url: str,
    workspace: Path,
    depth: str = "standard",
    headless: bool = True,
    *,
    network_isolation_available: bool = True,
) -> dict[str, Any]:
    """Explore a rendered bilingual target and atomically checkpoint schema-valid records."""
    if depth not in DEPTHS:
        raise ValueError(f"Unsupported exploration depth: {depth!r}")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Exploration URL must be an absolute HTTP(S) URL")

    policy = ExplorationPolicy(depth)
    ws = workspace.resolve()
    run_path = ws / "run.json"
    if run_path.is_file():
        run_state = load_run_state(ws)
        if run_state["depth"] != depth:
            raise ValueError("Existing workspace exploration depth does not match the requested depth")
    else:
        run_state = init_workspace(
            ws,
            f"RUN-{utc_now_iso()[:10].replace('-', '')}-0001-explore",
            "Sanitized browser target",
            depth=depth,
        )
    run_state["phase"] = "inventorying"
    save_run_state(ws, run_state, "Started policy-constrained bilingual UI inventory.")

    from playwright.sync_api import sync_playwright

    probe_results: dict[str, Any] = {}
    probe_messages: dict[str, dict[str, str]] = {"en": {}, "zh": {}}
    page_records: list[dict[str, Any]] = []
    menus: list[dict[str, Any]] = []
    blocked_note: str | None = None
    outcome_status = "done"
    browser = None
    context = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(service_workers="block")
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        _wait_for_stability(page)

        menu_controls = page.locator("[data-menu]").evaluate_all(
            "elements => elements.map(el => ({id: el.id, key: el.getAttribute('data-i18n')}))"
        )
        if not menu_controls:
            menu_controls = [{"id": None, "key": "page.root"}]
        if depth == "quick":
            menu_controls = menu_controls[:1]

        item_index = 1
        for menu_index, menu_control in enumerate(menu_controls, start=1):
            if menu_control.get("id"):
                _switch_language(page, "en")
                control = _by_id(page, str(menu_control["id"]))
                label = _normalized_text(control.inner_text())
                if not policy.allows_action(control_type="menu_item", label=label):
                    continue
                control.click()
                _wait_for_stability(page)
            en_snapshot = _visible_snapshot(page)
            _switch_language(page, "zh")
            zh_snapshot = _visible_snapshot(page)
            _switch_language(page, "en")

            menu_id = f"MN-{menu_index:04d}"
            page_id = f"PG-{menu_index:06d}"
            state_id = f"ST-{menu_index:06d}"
            menu_key = str(menu_control.get("key") or "page.root")
            en_name = _normalized_text(en_snapshot.get(menu_key, {}).get("text")) or menu_key
            zh_name = _normalized_text(zh_snapshot.get(menu_key, {}).get("text"))
            domain = _domain_for_key(menu_key)
            keys = sorted(set(en_snapshot) | set(zh_snapshot))
            items: list[dict[str, Any]] = []
            for key in keys:
                en_row, zh_row = en_snapshot.get(key), zh_snapshot.get(key)
                basis = en_row or zh_row or {}
                if not policy.keep_observation(
                    str((en_row or zh_row or {}).get("text", "")),
                    dynamic=bool(basis.get("dynamic")),
                ):
                    continue
                items.append(
                    _make_item(
                        item_index,
                        key,
                        en_row,
                        zh_row,
                        state_id=state_id,
                        hierarchy=en_name,
                    )
                )
                item_index += 1

            page_record = {
                "contract_version": CONTRACT_VERSION,
                "artifact_kind": "page_inventory",
                "page_id": page_id,
                "menu_id": menu_id,
                "menu_hierarchy_zh": [zh_name] if zh_name else [],
                "menu_hierarchy_en": [en_name],
                "domain_tag": domain,
                "observed_at": utc_now_iso(),
                "coverage_status": "fully_inspected",
                "states": [
                    {
                        "state_id": state_id,
                        "name": "Rendered default state",
                        "parent_condition": None,
                        "iframe_context": None,
                        "reproduce_steps": ["Open menu using its stable data-menu control."],
                        "coverage_status": "fully_inspected",
                        "blocker_reason": None,
                    }
                ],
                "items": items,
            }
            page_records.append(page_record)
            menus.append(
                {
                    "menu_id": menu_id,
                    "parent_menu_id": None,
                    "is_group": False,
                    "hierarchy_en": [en_name],
                    "hierarchy_zh": [zh_name] if zh_name else [],
                    "route_or_entry": f"#{menu_control.get('id')}" if menu_control.get("id") else url,
                    "page_id": page_id,
                    "page_record_path": f"pages/{page_id}.json",
                    "domain_tag": domain,
                    "coverage_status": "fully_inspected",
                    "blocker_reason": None,
                }
            )

        should_probe, blocked_note = policy.synthetic_probe_decision(
            network_isolation_available=network_isolation_available
        )
        if blocked_note:
            outcome_status = "skipped"
        if should_probe and page_records:
            probe_context = browser.new_context(service_workers="block")

            def block_websocket(ws_route: Any) -> None:
                ws_route.close(code=1008, reason="Synthetic UI probe network isolation")

            def abort_request(route: Any, request: Any) -> None:
                # Every request issued during synthetic input is unsafe, including
                # delayed fetch/XHR callbacks and mutating requests.
                route.abort()

            probe_context.route_web_socket("**/*", block_websocket)
            try:
                probe_page = probe_context.new_page()
                probe_page.goto(url, wait_until="domcontentloaded")
                _wait_for_stability(probe_page)
                probe_context.route("**/*", abort_request)
                first_menu = menu_controls[0]
                if first_menu.get("id"):
                    _by_id(probe_page, str(first_menu["id"])).click()
                probe_results, probe_messages = _probe_inputs(probe_page, policy)
            except RuntimeError as exc:
                blocked_note = str(exc)
                outcome_status = "blocked"
            finally:
                probe_context.close()
            first_record = page_records[0]
            for key in sorted(set(probe_messages["en"]) | set(probe_messages["zh"])):
                en_text = probe_messages["en"].get(key)
                zh_text = probe_messages["zh"].get(key)
                first_record["items"].append(
                    _make_item(
                        item_index,
                        key,
                        {"tag": "div", "text": en_text, "disabled": False} if en_text else None,
                        {"tag": "div", "text": zh_text, "disabled": False} if zh_text else None,
                        state_id=first_record["states"][0]["state_id"],
                        hierarchy=first_record["menu_hierarchy_en"][0],
                        synthetic_probe=True,
                    )
                )
                item_index += 1
        if not context.pages or not context.pages[0].is_closed():
            context.close()
        browser.close()

    observed_at = utc_now_iso()
    menu_map = {
        "contract_version": CONTRACT_VERSION,
        "artifact_kind": "menu_map",
        "observed_at": observed_at,
        "menus": menus,
    }
    _validate("menu_map", menu_map)
    for record in page_records:
        _validate("page_inventory", record)
    atomic_write_json(ws / "records" / "menu-map.json", menu_map)
    for record in page_records:
        atomic_write_json(ws / "records" / "pages" / f"{record['page_id']}.json", record)

    run_state["task_units"] = []
    for record in page_records:
        for language in ("en", "zh"):
            run_state["task_units"].append(
                {
                    "unit_id": f"inventory:{record['page_id']}:{language}",
                    "menu_id": record["menu_id"],
                    "page_id": record["page_id"],
                    "state_id": record["states"][0]["state_id"],
                    "language": language,
                    "status": "done",
                    "blocker_reason": None,
                    "updated_at": observed_at,
                }
            )
    if blocked_note and page_records:
        record = page_records[0]
        run_state["task_units"].append(
            {
                "unit_id": f"synthetic-probe:{record['page_id']}:en",
                "menu_id": record["menu_id"],
                "page_id": record["page_id"],
                "state_id": record["states"][0]["state_id"],
                "language": "en",
                "status": "blocked" if outcome_status == "blocked" else "skipped",
                "blocker_reason": blocked_note,
                "updated_at": observed_at,
            }
        )
    snapshot_source = json.dumps([menu_map, *page_records], ensure_ascii=False, sort_keys=True)
    run_state["inventory_snapshot_hash"] = sha256_text(snapshot_source)
    run_state["phase"] = "inventory_frozen"
    save_run_state(ws, run_state, blocked_note or "Bilingual inventory and policy-safe probes completed.")
    atomic_write_text(
        ws / "findings.md",
        f"# Findings ({run_state['run_id']})\n\n"
        f"- Captured {len(menus)} visible menu(s) and {sum(len(r['items']) for r in page_records)} static UI observation(s).\n"
        + (f"- {blocked_note}\n" if blocked_note else "- Synthetic probes completed with outbound HTTP and WebSocket blocking active.\n" if depth == "thorough" else ""),
    )
    return {
        "status": outcome_status if blocked_note else "done",
        "note": blocked_note or "Exploration completed.",
        "workspace": ws.as_posix(),
        "menu_count": len(menus),
        "page_count": len(page_records),
        "probe_results": probe_results,
    }
