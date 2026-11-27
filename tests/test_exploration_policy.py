from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from enterprise_i18n_ops.contracts import validate_file
from enterprise_i18n_ops.explore.policy import ExplorationPolicy
from enterprise_i18n_ops.explore.runner import explore_target

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "demo_app"


class _RecordingHandler(SimpleHTTPRequestHandler):
    posts: ClassVar[list[str]] = []

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, directory=str(FIXTURE_DIR), **kwargs)

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        self.posts.append(self.path)
        self.send_response(204)
        self.end_headers()


@pytest.fixture
def demo_server():  # type: ignore[no-untyped-def]
    _RecordingHandler.posts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html", _RecordingHandler.posts
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@contextmanager
def _html_server(html: str) -> Iterator[tuple[str, list[str]]]:
    posts: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            posts.append(self.path)
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html", posts
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _page_items(workspace: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for path in sorted((workspace / "records" / "pages").glob("*.json")):
        items.extend(json.loads(path.read_text(encoding="utf-8"))["items"])
    return items


def test_policy_blocks_mutating_actions_and_unsafe_enter() -> None:
    policy = ExplorationPolicy("thorough")
    for label in ("Submit", "Save", "Execute", "Approve", "Delete", "Import", "Export", "保存"):
        assert policy.allows_action(control_type="button", label=label) is False
    assert policy.allows_key("Enter", synthetic_probe=True) is False
    assert policy.allows_action(control_type="button", label="Reset") is True


def test_standard_discovers_bilingual_static_ui_without_backend_writes(
    tmp_path: Path, demo_server
) -> None:  # type: ignore[no-untyped-def]
    url, posts = demo_server
    result = explore_target(url, tmp_path / "ws", depth="standard")

    assert result["status"] == "done"
    assert posts == []
    menu_map = tmp_path / "ws" / "records" / "menu-map.json"
    assert validate_file("menu_map", menu_map) == []
    page_files = sorted((tmp_path / "ws" / "records" / "pages").glob("*.json"))
    assert page_files
    assert all(validate_file("page_inventory", path) == [] for path in page_files)
    items = _page_items(tmp_path / "ws")
    assert any(item["i18n_key_or_code"] == "fx.spot.settlement_instruction" for item in items)
    assert any(item["en_text"] == "=SpotRate + Margin" for item in items)
    assert any(item["en_text"] == "Counterparty" and item["zh_text"] == "反对党" for item in items)


def test_thorough_probe_captures_bilingual_validation_and_aborts_autosave(
    tmp_path: Path, demo_server
) -> None:  # type: ignore[no-untyped-def]
    url, posts = demo_server
    result = explore_target(url, tmp_path / "ws", depth="thorough")

    assert result["status"] == "done"
    assert posts.count("/api/autosave") == 0
    assert posts.count("/api/submit") == 0
    assert result["probe_results"]["inp-cpty"]["reset_verified"] is True
    items = _page_items(tmp_path / "ws")
    validation = [item for item in items if item["control_type"] == "validation_message"]
    assert any(item["en_text"] == "Counterparty code must be 4-12 uppercase letters." for item in validation)
    assert any(item["zh_text"] == "交易对手代码须为4-12位大写字母。" for item in validation)


def test_thorough_probe_destroys_debounced_autosave_before_it_can_post(tmp_path: Path) -> None:
    html = """<!doctype html><html lang="en"><head><style>.hidden{display:none}</style></head><body>
    <button id="lang-toggle" type="button">Language</button>
    <button id="menu:fx.spot" data-menu="fx" data-i18n="nav.fx">FX</button>
    <label for="form:cpty.code" data-i18n="fx.cpty">Counterparty</label>
    <input id="form:cpty.code" name="counterparty">
    <div id="validation" class="err hidden" data-i18n="fx.validation">Invalid code</div>
    <script>
      let lang = 'en';
      document.getElementById('lang-toggle').onclick = () => {
        lang = lang === 'en' ? 'zh' : 'en';
        document.documentElement.lang = lang;
        document.querySelector('[data-i18n="nav.fx"]').textContent = lang === 'en' ? 'FX' : '外汇';
        document.querySelector('[data-i18n="fx.cpty"]').textContent = lang === 'en' ? 'Counterparty' : '交易对手';
        document.querySelector('[data-i18n="fx.validation"]').textContent = lang === 'en' ? 'Invalid code' : '代码无效';
      };
      const input = document.getElementById('form:cpty.code');
      let timer;
      const autosave = () => {
        clearTimeout(timer);
        timer = setTimeout(() => fetch('/api/autosave', {method: 'POST'}).catch(() => {}), 300);
      };
      input.addEventListener('input', autosave);
      input.addEventListener('blur', autosave);
      input.addEventListener('input', () => {
        document.getElementById('validation').classList.toggle('hidden', !input.value);
      });
    </script></body></html>"""
    with _html_server(html) as (url, posts):
        result = explore_target(url, tmp_path / "ws", depth="thorough")
        time.sleep(0.5)

    assert result["status"] == "done"
    assert posts.count("/api/autosave") == 0


@pytest.mark.parametrize("depth", ["standard", "thorough"])
def test_special_character_ids_work_in_all_probe_modes(tmp_path: Path, depth: str) -> None:
    html = """<!doctype html><html lang="en"><head><style>.hidden{display:none}</style></head><body>
    <button id="lang-toggle" type="button">Language</button>
    <button id="menu:fx.spot" data-menu="fx" data-i18n="nav.fx">FX</button>
    <label for="form:cpty.code" data-i18n="fx.cpty">Counterparty</label>
    <input id="form:cpty.code" name="counterparty">
    <div id="validation" class="err hidden" data-i18n="fx.validation">Invalid code</div>
    <script>
      let lang = 'en';
      document.getElementById('lang-toggle').onclick = () => {
        lang = lang === 'en' ? 'zh' : 'en';
        document.documentElement.lang = lang;
        document.querySelector('[data-i18n="nav.fx"]').textContent = lang === 'en' ? 'FX' : '外汇';
        document.querySelector('[data-i18n="fx.cpty"]').textContent = lang === 'en' ? 'Counterparty' : '交易对手';
        document.querySelector('[data-i18n="fx.validation"]').textContent = lang === 'en' ? 'Invalid code' : '代码无效';
      };
      const input = document.getElementById('form:cpty.code');
      input.addEventListener('input', () => {
        document.getElementById('validation').classList.toggle('hidden', !input.value);
      });
    </script></body></html>"""
    with _html_server(html) as (url, posts):
        result = explore_target(url, tmp_path / depth, depth=depth)

    assert result["status"] == "done"
    assert posts == []
    if depth == "thorough":
        assert result["probe_results"]["form:cpty.code"]["reset_verified"] is True


def test_thorough_without_isolation_skips_probes_and_writes_nothing(
    tmp_path: Path, demo_server
) -> None:  # type: ignore[no-untyped-def]
    url, posts = demo_server
    result = explore_target(
        url,
        tmp_path / "ws",
        depth="thorough",
        network_isolation_available=False,
    )

    assert result["status"] == "skipped"
    assert "network isolation" in result["note"].lower()
    assert result["probe_results"] == {}
    assert posts == []
    run_state = json.loads((tmp_path / "ws" / "run.json").read_text(encoding="utf-8"))
    assert any(unit["status"] == "skipped" for unit in run_state["task_units"])
