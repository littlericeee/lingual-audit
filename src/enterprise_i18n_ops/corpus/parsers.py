"""Local document parsers producing records in the frozen corpus contract."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

import pdfplumber
from bs4 import BeautifulSoup, NavigableString
from bs4.element import Comment, Tag
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from pdfplumber.page import Page

from enterprise_i18n_ops.contracts import sha256_text

from .normalize import normalize_text

Record = dict[str, Any]

_HEADERS = {
    "en_term": ("en_term", "english", "english term", "term", "en", "英文", "英文术语"),
    "zh_term": ("zh_term", "chinese", "chinese term", "zh", "中文", "中文术语", "译名", "譯名"),
    "field_code": ("field_code", "field", "code", "字段编码"),
    "aliases": ("aliases", "alias", "synonyms", "别名"),
    "domain": ("domain", "domain_tags", "领域"),
    "definition": ("definition", "description", "notes", "定义", "释义"),
}
_CLAUSE = re.compile(
    r"^\s*(第\s*[\d一二三四五六七八九十百零〇.]+\s*[条條章节節款]"
    r"|(?:Article|Clause|Section)\s+\d+(?:\.\d+)*[A-Za-z]?"
    r"|\d+(?:\.\d+)+[.)]?(?=\s|[：:])|\d+[.)](?=\s))",
    re.IGNORECASE,
)
_PRINTED_PAGE = re.compile(r"^\s*(?:Page\s+(\d+)|第\s*(\d+)\s*页)(?!\d)", re.IGNORECASE)
_QUOTED = r'[“「『"]([^”」』"\n]+)[”」』"]'
_PAIR = re.compile(
    r"^(?P<english>[A-Za-z][A-Za-z0-9'& /_-]*?)"
    r"(?:\s*[（(](?P<aliases>[A-Za-z][A-Za-z0-9'& /_-]*)[）)])?"
    r"(?=\s*[\u3400-\u9fff])[^：:;；。]*?"
    r"(?:中文(?:标准|標準)?[译譯]名(?:为|為)|[译譯]作)\s*" + _QUOTED
)
_ALTERNATIVE = re.compile(r"\s*(?:或(?:者)?|、|/|和|及)\s*" + _QUOTED)
_EN_DEFINITION = re.compile(r"^\s*([A-Za-z][A-Za-z0-9'& /_-]*):[ \t]+(\S.*)", re.DOTALL)
_TABULAR_KINDS = {"bilingual_glossary", "field_dictionary"}
_HTML_BLOCKS = {"p", "dl", "li", "blockquote", "pre"}
_HTML_CONTAINERS = {"article", "section", "main", "div", "header", "footer", "ul", "ol"}


def _clause(text: str) -> str | None:
    match = _CLAUSE.match(text)
    return match[1] if match else None


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def _keys(text: str) -> str:
    return re.sub(r"[^\w]|_", "", normalize_text(text))


def _columns(headers: Sequence[str], mapping: dict[str, str]) -> dict[str, int]:
    keys = [_keys(re.sub(r"\s*\([^()]*\)\s*$", "", normalize_text(value))) for value in headers]
    exact_keys = [_keys(value) for value in headers]
    positions = {}
    for field_name, aliases in _HEADERS.items():
        custom = mapping.get(field_name)
        if custom:
            key = _keys(custom)
            if key in exact_keys:
                positions[field_name] = exact_keys.index(key)
            continue
        for alias in aliases:
            key = _keys(alias)
            if key in keys:
                positions[field_name] = keys.index(key)
                break
    return positions


def _tokens(text: str) -> list[str]:
    return [token.strip() for token in re.split(r"[|;；\n]+", text) if token.strip()]


def _bbox_union(boxes: Sequence[Sequence[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


@dataclass
class ParsedDocument:
    document: Record
    contract_version: str
    entries: list[Record] = field(default_factory=list)
    chunks: list[Record] = field(default_factory=list)
    warnings: list[Record] = field(default_factory=list)

    def _metadata(self, locator: Record, text: str) -> Record:
        return {
            **{
                key: self.document[key]
                for key in (
                    "doc_id",
                    "doc_version",
                    "domain_tags",
                    "region_scope",
                    "normative_level",
                )
            },
            "contract_version": self.contract_version,
            "target_locale": self.document.get("target_locale"),
            "locator": locator,
            "excerpt_hash": sha256_text(text),
        }

    def _record_id(self, prefix: str, source_index: int, locator: Record) -> str:
        identity = (self.document["doc_id"], self.document["doc_version"], locator, source_index)
        digest = sha256_text(json.dumps(identity, sort_keys=True))[:48]
        return f"{prefix}-{digest.upper()}"

    def add_entry(
        self, values: dict[str, str], locator: Record, excerpt: str, *, verified: bool = True
    ) -> None:
        english = values.get("en_term", "")
        if not normalize_text(english):
            return
        if self.document["language_scope"] == "zh_only":
            return
        chinese = values.get("zh_term") or None
        if self.document["language_scope"] == "en_only" or not (chinese and chinese.strip()):
            chinese = None
        record = self._metadata(locator, excerpt)
        domains = [
            tag
            for tag in re.split(r"[,，|;；\s]+", values.get("domain", ""))
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", tag)
        ]
        record.update(
            artifact_kind="glossary_entry",
            glossary_id=self._record_id("GLS", len(self.entries), locator),
            en_term=english,
            en_normalized=normalize_text(english),
            en_aliases=_tokens(values.get("aliases", "")),
            zh_term=chinese,
            zh_normalized=normalize_text(chinese) if chinese else None,
            definition=values.get("definition") or None,
            field_code=values.get("field_code") or None,
            domain_tags=domains or record["domain_tags"],
            support_type="bilingual_equivalent"
            if chinese and verified
            else "concept_definition_only",
            excerpt=excerpt,
            pairing_verified=verified,
        )
        self.entries.append(record)

    def add_chunk(
        self, text: str, locator: Record, headings: Sequence[str], parent_clause: str | None
    ) -> None:
        normalized = normalize_text(text)
        if not normalized:
            return
        language = {"en_only": "en", "zh_only": "zh", "bilingual_en_zh": "bilingual"}[
            self.document["language_scope"]
        ]
        self.chunks.append(
            {
                **self._metadata(locator, text),
                "artifact_kind": "document_chunk",
                "chunk_id": self._record_id("CHK", len(self.chunks), locator),
                "parent_clause": parent_clause,
                "heading_path": list(headings),
                "text_verbatim": text,
                "text_normalized": normalized,
                "language": language,
                "support_type": "concept_definition_only",
            }
        )


def _term_rows(
    rows: Iterable[tuple[int, Sequence[Any]]],
    mapping: dict[str, str],
) -> Iterator[tuple[int, list[str], dict[str, str]]]:
    columns: dict[str, int] = {}
    header = []
    for row_index, row in rows:
        cells = list(map(_cell, row))
        if not columns:
            columns = _columns(cells, mapping)
            if columns.get("en_term") is None:
                columns.clear()
            else:
                header = [_keys(cell) for cell in cells]
            continue
        if [_keys(cell) for cell in cells] == header:
            continue
        values = {name: cells[index] for name, index in columns.items() if index < len(cells)}
        if values.get("en_term", "").strip():
            yield row_index, cells, values


def _add_spreadsheet_records(
    result: ParsedDocument,
    rows: Iterable[tuple[int, Sequence[Any]]],
    sheet_name: str,
) -> None:
    for row_index, cells, values in _term_rows(rows, result.document.get("xlsx_columns", {})):
        populated = [index for index, cell in enumerate(cells, 1) if cell]
        first, last = map(get_column_letter, (populated[0], populated[-1]))
        locator = {
            "format": "xlsx",
            "sheet_name": sheet_name,
            "cell_range": f"{first}{row_index}:{last}{row_index}",
            "field_code": values.get("field_code") or None,
            "row_index": row_index,
            "file_sha256": result.document["file_sha256"],
        }
        result.add_entry(values, locator, "\t".join(cells[: populated[-1]]))


def _parse_csv(path: Path, result: ParsedDocument) -> None:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        headers = reader.fieldnames
        if headers is None:
            return
        if len(set(headers)) != len(headers):
            raise ValueError(f"Duplicate CSV column names for {result.document['doc_id']}")

        def rows() -> Iterator[tuple[int, Sequence[Any]]]:
            yield 1, headers
            for index, row in enumerate(reader, 2):
                yield index, [*(row[header] for header in headers), *(row.get(None) or [])]

        _add_spreadsheet_records(result, rows(), "CSV")


def _parse_xlsx(path: Path, result: ParsedDocument) -> None:
    workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        for sheet in workbook.worksheets:
            rows = enumerate(sheet.iter_rows(values_only=True), 1)
            _add_spreadsheet_records(result, rows, sheet.title)
    finally:
        workbook.close()


def _html_text(node: Tag) -> str:
    parts = []
    for item in node.descendants:
        if isinstance(item, Comment):
            continue
        if isinstance(item, NavigableString):
            parts.append(str(item))
        elif item.name in {"br", "dd", "dt", "p", "li"} and parts:
            parts.append("\n")
    return "".join(parts)


def _html_pairs(text: str, locator: Record, result: ParsedDocument) -> None:
    scope = result.document["language_scope"]
    if scope == "en_only":
        definition = _EN_DEFINITION.match(text)
        if definition:
            result.add_entry({"en_term": definition[1], "definition": definition[2]}, locator, text)
        return
    if scope != "bilingual_en_zh":
        return
    for sentence in re.split(r"[；;]", text):
        label = _clause(sentence)
        if label:
            sentence = sentence.lstrip()[len(label) :].lstrip(" ：:")
        match = _PAIR.match(sentence.lstrip())
        if not match:
            continue
        tail = sentence.lstrip()[match.end() :]
        translations = [match[3]]
        while alternative := _ALTERNATIVE.match(tail):
            translations.append(alternative[1])
            tail = tail[alternative.end() :]
        for chinese in translations:
            result.add_entry(
                {
                    "en_term": match["english"].rstrip(),
                    "zh_term": chinese,
                    "aliases": match["aliases"] or "",
                },
                locator,
                text,
                verified=len(translations) == 1,
            )


def _parse_html(path: Path, result: ParsedDocument) -> None:
    soup = BeautifulSoup(path.read_bytes(), "html.parser", from_encoding="utf-8")
    for node in soup.find_all(["script", "style", "nav", "head", "template"]):
        node.decompose()
    heading_levels: dict[int, str] = {}
    file_hash = result.document["file_sha256"]

    def walk(container: Tag, inherited_anchor: str | None, parent_clause: str | None) -> None:
        inline: list[str] = []
        active_clause = parent_clause

        def emit(text: str, anchor: str | None, *, extract_pairs: bool = True) -> None:
            nonlocal active_clause
            headings = list(heading_levels.values())
            locator = {
                "format": "html",
                "file_sha256": file_hash,
                "heading_path": headings,
                "anchor": anchor,
            }
            active_clause = _clause(text) or active_clause
            result.add_chunk(text, locator, headings, active_clause)
            if extract_pairs:
                _html_pairs(text, locator, result)

        for child in container.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                inline.append(str(child))
                continue
            name = child.name
            is_heading = name in {"h1", "h2", "h3", "h4", "h5", "h6"}
            if not is_heading and name not in _HTML_BLOCKS | _HTML_CONTAINERS | {"table"}:
                inline.append("\n" if name == "br" else _html_text(child))
                continue
            emit("".join(inline), inherited_anchor)
            inline.clear()
            anchor = str(child["id"]) if child.get("id") else inherited_anchor
            if is_heading:
                level = int(name[1])
                for old_level in tuple(heading_levels):
                    if old_level >= level:
                        del heading_levels[old_level]
                text = _html_text(child)
                heading_levels[level] = text.strip()
                active_clause = _clause(text)
                emit(text, anchor, extract_pairs=False)
            elif name == "table":
                rows = (
                    (
                        index,
                        [_html_text(cell) for cell in row.find_all(["th", "td"], recursive=False)],
                    )
                    for index, row in enumerate(
                        (row for row in child.find_all("tr") if row.find_parent("table") is child),
                        1,
                    )
                )
                entries_before = len(result.entries)
                for row_index, cells, values in _term_rows(rows, {}):
                    locator = {
                        "format": "html",
                        "file_sha256": file_hash,
                        "heading_path": list(heading_levels.values()),
                        "anchor": anchor,
                        "table_row": row_index,
                    }
                    result.add_entry(values, locator, "\t".join(cells))
                if len(result.entries) == entries_before:
                    emit(child.get_text("\n"), anchor, extract_pairs=False)
            elif name in _HTML_BLOCKS:
                emit(_html_text(child), anchor)
            else:
                old_headings = heading_levels.copy()
                walk(child, anchor, active_clause)
                if name in {"article", "section"}:
                    heading_levels.clear()
                    heading_levels.update(old_headings)
        emit("".join(inline), inherited_anchor)

    walk(soup.body or soup, None, None)


def _parse_pdf_page(
    page: Page,
    raw_text: str,
    result: ParsedDocument,
    headings: list[str],
    parent_clause: str | None,
) -> str | None:
    printed_page = next(
        (
            match[1] or match[2]
            for line in raw_text.splitlines()
            if (match := _PRINTED_PAGE.match(line))
        ),
        None,
    )
    block_index = 0

    def locator(bbox: Sequence[float]) -> Record:
        nonlocal block_index
        value = {
            "format": "pdf",
            "physical_page": page.page_number,
            "printed_page": printed_page,
            "clause": parent_clause,
            "block_index": block_index,
            "bbox": list(bbox),
        }
        block_index += 1
        return value

    tables = page.find_tables()
    borderless = not tables and result.document["content_kind"] in _TABULAR_KINDS
    if borderless:
        tables = page.find_tables({"vertical_strategy": "text", "horizontal_strategy": "text"})
    text_tables = []
    for table in tables:
        rows = table.extract()
        terms = list(_term_rows(enumerate(rows), {}))
        if result.document["language_scope"] == "zh_only":
            terms = []
        if terms or not borderless:
            text_tables.append((table, rows, terms))

    def outside_tables(obj: Record) -> bool:
        center_x = (obj["x0"] + obj["x1"]) / 2
        center_y = (obj["top"] + obj["bottom"]) / 2
        return not any(
            table.bbox[0] <= center_x <= table.bbox[2]
            and table.bbox[1] <= center_y <= table.bbox[3]
            for table, _, _ in text_tables
        )

    text_page = page.filter(outside_tables) if text_tables else page
    lines = text_page.extract_text_lines(strip=False, return_chars=True)
    sizes = [median(char["size"] for char in line["chars"]) for line in lines]
    body_size = median(sizes) if sizes else 0
    blocks = [
        (line["top"], line["x0"], "line", (line, size))
        for line, size in zip(lines, sizes, strict=True)
    ]
    blocks.extend(
        (table.bbox[1], table.bbox[0], "table", (table, rows, terms))
        for table, rows, terms in text_tables
    )
    pending_lines: list[Record] = []

    def flush() -> None:
        if pending_lines:
            boxes = [
                (line["x0"], line["top"], line["x1"], line["bottom"]) for line in pending_lines
            ]
            text = "\n".join(line["text"] for line in pending_lines)
            result.add_chunk(text, locator(_bbox_union(boxes)), headings, parent_clause)
            pending_lines.clear()

    for _, _, kind, data in sorted(blocks, key=lambda item: item[:2]):
        if kind == "table":
            flush()
            table, rows, terms = data
            if terms:
                for row_index, cells, values in terms:
                    result.add_entry(values, locator(table.rows[row_index].bbox), "\t".join(cells))
            else:
                text = "\n".join("\t".join(map(_cell, row)) for row in rows)
                result.add_chunk(text, locator(table.bbox), headings, parent_clause)
            continue

        line, size = data
        label = _clause(line["text"])
        is_heading = size > body_size * 1.12
        if pending_lines:
            previous = pending_lines[-1]
            gap = line["top"] - previous["bottom"]
            if label or is_heading or gap > (previous["bottom"] - previous["top"]) * 0.9:
                flush()
        if is_heading:
            title = line["text"].strip()
            headings[:] = [title] if size > body_size * 1.35 else [*headings[:1], title]
            parent_clause = label
        elif label:
            parent_clause = label
        pending_lines.append(line)
        if is_heading:
            flush()
    flush()
    return parent_clause


def _parse_pdf(path: Path, result: ParsedDocument) -> None:
    headings: list[str] = []
    parent_clause = None
    has_text = False
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            raw_text = page.extract_text()
            if normalize_text(raw_text):
                has_text = True
                parent_clause = _parse_pdf_page(page, raw_text, result, headings, parent_clause)

    if not has_text:
        result.warnings.append(
            {
                "doc_id": result.document["doc_id"],
                "code": "pdf_no_extractable_text",
                "message": "PDF contains no extractable text.",
            }
        )


_PARSERS = {"pdf": _parse_pdf, "html": _parse_html, "xlsx": _parse_xlsx, "csv": _parse_csv}


def parse_document(path: Path, document: Record, contract_version: str) -> ParsedDocument:
    result = ParsedDocument(document, contract_version)
    _PARSERS[document["format"]](path, result)
    return result
