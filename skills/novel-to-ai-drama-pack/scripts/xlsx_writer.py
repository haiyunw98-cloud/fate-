#!/usr/bin/env python3
"""Small, dependency-free Office Open XML spreadsheet writer."""

from __future__ import annotations

import math
import tempfile
import zipfile
from itertools import chain
from pathlib import Path
from typing import Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", REL_NS)
MAX_CELL_CHARS = 32_767
TRUNCATION_SUFFIX = "…[Excel单元格已截断，完整内容见 project.md]"
INVALID_SHEET_NAME_CHARACTERS = frozenset("[]:*?/\\")


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _safe_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = "".join(character if _is_xml_1_0_character(character) else "\ufffd" for character in text)
    if len(text) > MAX_CELL_CHARS:
        text = text[:MAX_CELL_CHARS - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX
    return text


def _is_xml_1_0_character(character: str) -> bool:
    codepoint = ord(character)
    if codepoint in (0x09, 0x0A, 0x0D):
        return True
    if not (0x20 <= codepoint <= 0xD7FF or 0xE000 <= codepoint <= 0xFFFD or 0x10000 <= codepoint <= 0x10FFFF):
        return False
    # Unicode noncharacters are unsuitable for portable Office XML even where
    # the XML grammar's broad scalar range would otherwise admit them.
    return not (0xFDD0 <= codepoint <= 0xFDEF or (codepoint & 0xFFFF) in (0xFFFE, 0xFFFF))


def _xml(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _content_types() -> bytes:
    root = ET.Element("Types", xmlns=CONTENT_NS)
    ET.SubElement(root, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(root, "Default", Extension="xml", ContentType="application/xml")
    ET.SubElement(root, "Override", PartName="/xl/workbook.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    ET.SubElement(root, "Override", PartName="/xl/worksheets/sheet1.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
    ET.SubElement(root, "Override", PartName="/xl/styles.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml")
    return _xml(root)


def _root_relationships() -> bytes:
    root = ET.Element(f"{{{PKG_REL_NS}}}Relationships")
    ET.SubElement(root, f"{{{PKG_REL_NS}}}Relationship", Id="rId1", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", Target="xl/workbook.xml")
    return _xml(root)


def _workbook(sheet_name: str) -> bytes:
    root = ET.Element(f"{{{MAIN_NS}}}workbook")
    sheets = ET.SubElement(root, f"{{{MAIN_NS}}}sheets")
    ET.SubElement(sheets, f"{{{MAIN_NS}}}sheet", name=sheet_name, sheetId="1", **{f"{{{REL_NS}}}id": "rId1"})
    return _xml(root)


def _workbook_relationships() -> bytes:
    root = ET.Element(f"{{{PKG_REL_NS}}}Relationships")
    ET.SubElement(root, f"{{{PKG_REL_NS}}}Relationship", Id="rId1", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", Target="worksheets/sheet1.xml")
    ET.SubElement(root, f"{{{PKG_REL_NS}}}Relationship", Id="rId2", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles", Target="styles.xml")
    return _xml(root)


def _styles() -> bytes:
    root = ET.Element(f"{{{MAIN_NS}}}styleSheet")
    fonts = ET.SubElement(root, f"{{{MAIN_NS}}}fonts", count="2")
    font = ET.SubElement(fonts, f"{{{MAIN_NS}}}font")
    ET.SubElement(font, f"{{{MAIN_NS}}}sz", val="11")
    ET.SubElement(font, f"{{{MAIN_NS}}}name", val="Arial")
    header_font = ET.SubElement(fonts, f"{{{MAIN_NS}}}font")
    ET.SubElement(header_font, f"{{{MAIN_NS}}}b")
    ET.SubElement(header_font, f"{{{MAIN_NS}}}sz", val="11")
    ET.SubElement(header_font, f"{{{MAIN_NS}}}name", val="Arial")
    fills = ET.SubElement(root, f"{{{MAIN_NS}}}fills", count="2")
    ET.SubElement(ET.SubElement(fills, f"{{{MAIN_NS}}}fill"), f"{{{MAIN_NS}}}patternFill", patternType="none")
    ET.SubElement(ET.SubElement(fills, f"{{{MAIN_NS}}}fill"), f"{{{MAIN_NS}}}patternFill", patternType="gray125")
    borders = ET.SubElement(root, f"{{{MAIN_NS}}}borders", count="1")
    border = ET.SubElement(borders, f"{{{MAIN_NS}}}border")
    for side in ("left", "right", "top", "bottom", "diagonal"):
        ET.SubElement(border, f"{{{MAIN_NS}}}{side}")
    cell_style_xfs = ET.SubElement(root, f"{{{MAIN_NS}}}cellStyleXfs", count="1")
    ET.SubElement(cell_style_xfs, f"{{{MAIN_NS}}}xf", numFmtId="0", fontId="0", fillId="0", borderId="0")
    cell_xfs = ET.SubElement(root, f"{{{MAIN_NS}}}cellXfs", count="3")
    ET.SubElement(cell_xfs, f"{{{MAIN_NS}}}xf", numFmtId="0", fontId="0", fillId="0", borderId="0", xfId="0")
    ET.SubElement(cell_xfs, f"{{{MAIN_NS}}}xf", numFmtId="0", fontId="1", fillId="0", borderId="0", xfId="0", applyFont="1")
    wrapped = ET.SubElement(cell_xfs, f"{{{MAIN_NS}}}xf", numFmtId="0", fontId="0", fillId="0", borderId="0", xfId="0", applyAlignment="1")
    ET.SubElement(wrapped, f"{{{MAIN_NS}}}alignment", wrapText="1", vertical="top")
    styles = ET.SubElement(root, f"{{{MAIN_NS}}}cellStyles", count="1")
    ET.SubElement(styles, f"{{{MAIN_NS}}}cellStyle", name="Normal", xfId="0", builtinId="0")
    return _xml(root)


def _cell_xml(value: object, row_number: int, column_number: int) -> str:
    reference = f"{_column_name(column_number)}{row_number}"
    style = "1" if row_number == 1 else "2"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'
    text = _safe_text(value)
    preserve = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t{preserve}>{escape(text)}</t></is></c>'


def _write_worksheet(path: Path, headers: Sequence[str], rows: Sequence[Sequence[object]], widths: Sequence[float]) -> None:
    """Stream worksheet XML one row at a time to bound peak memory."""
    row_count = len(rows) + 1
    last_column = _column_name(len(headers))
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write('<?xml version="1.0" encoding="utf-8"?>\n')
        handle.write(f'<worksheet xmlns="{MAIN_NS}">')
        handle.write('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen" /></sheetView></sheetViews>')
        handle.write("<cols>")
        for index, width in enumerate(widths, start=1):
            handle.write(f'<col min="{index}" max="{index}" width="{width}" customWidth="1" />')
        handle.write("</cols><sheetData>")
        for row_number, values in enumerate(chain((headers,), rows), start=1):
            cells = (_cell_xml(value, row_number, column_number) for column_number, value in enumerate(values, start=1))
            handle.write(f'<row r="{row_number}">{"".join(cells)}</row>\n')
        handle.write(f'</sheetData><autoFilter ref="A1:{last_column}{row_count}" /></worksheet>')


def write_xlsx(path: Path, headers: Sequence[str], rows: Sequence[Sequence[object]], *, sheet_name: str = "镜头表", widths: Sequence[float] | None = None) -> None:
    """Write one-sheet OOXML workbook to *path*."""
    if not headers:
        raise ValueError("headers must not be empty")
    if any(len(row) != len(headers) for row in rows):
        raise ValueError("every row must have the same number of cells as headers")
    if not sheet_name or len(sheet_name) > 31 or any(character in INVALID_SHEET_NAME_CHARACTERS for character in sheet_name):
        raise ValueError("sheet_name must be 1..31 characters and must not contain []:*?/\\")
    effective_widths = list(widths or [18.0] * len(headers))
    if len(effective_widths) != len(headers):
        raise ValueError("width count must match headers")
    if any(not isinstance(width, (int, float)) or isinstance(width, bool) or not math.isfinite(width) or not 0 < width <= 255 for width in effective_widths):
        raise ValueError("each width must be a finite number in the range (0, 255]")
    members = {
        "[Content_Types].xml": _content_types(),
        "_rels/.rels": _root_relationships(),
        "xl/workbook.xml": _workbook(sheet_name),
        "xl/_rels/workbook.xml.rels": _workbook_relationships(),
        "xl/styles.xml": _styles(),
    }
    with tempfile.TemporaryDirectory(prefix=".xlsx-writer-", dir=path.parent) as temp:
        worksheet_path = Path(temp) / "sheet1.xml"
        _write_worksheet(worksheet_path, headers, rows, effective_widths)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in members.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
            worksheet_info = zipfile.ZipInfo(
                "xl/worksheets/sheet1.xml", date_time=(1980, 1, 1, 0, 0, 0)
            )
            worksheet_info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(worksheet_info, worksheet_path.read_bytes())
