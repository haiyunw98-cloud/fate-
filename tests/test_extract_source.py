from __future__ import annotations

import builtins
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

import extract_source
from extract_source import SourceExtractionError, extract_text


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "novel-to-ai-drama-pack"
    / "scripts"
    / "extract_source.py"
)


def _write_docx(path: Path, document_xml: str | None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if document_xml is not None:
            archive.writestr("word/document.xml", document_xml)


def _write_epub(
    path: Path,
    *,
    include_container: bool = True,
    include_second_document: bool = True,
) -> None:
    container_xml = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    package_xml = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="chapter-one" href="text/chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter-two" href="text/chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter-one"/>
    <itemref idref="chapter-two"/>
  </spine>
</package>
"""
    chapter_one = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>不应提取的书名</title></head>
<body><h1>第一章</h1><p>开端&amp;相遇</p><ul><li>线索一</li></ul></body>
</html>"""
    chapter_two = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>也不应出现</title></head>
<body><h2>最后一章</h2><p>故事结局</p></body>
</html>"""

    with zipfile.ZipFile(path, "w") as archive:
        if include_container:
            archive.writestr("META-INF/container.xml", container_xml)
        archive.writestr("OEBPS/content.opf", package_xml)
        archive.writestr("OEBPS/text/chapter1.xhtml", chapter_one)
        if include_second_document:
            archive.writestr("OEBPS/text/chapter2.xhtml", chapter_two)


def test_extracts_utf8_markdown_from_first_through_last_chapter(tmp_path: Path) -> None:
    source = tmp_path / "novel.md"
    source.write_text("# 第一章\n开端\n\n# 最后一章\n结局", encoding="utf-8")

    result = extract_text(source)

    assert "第一章" in result
    assert "最后一章" in result
    assert result.endswith("结局")


def test_plain_text_falls_back_to_gb18030(tmp_path: Path) -> None:
    source = tmp_path / "旧编码.txt"
    source.write_bytes("第一章：旧城\n最终结局".encode("gb18030"))

    assert extract_text(source) == "第一章：旧城\n最终结局"


def test_plain_text_reports_encoding_failure_without_replacement(tmp_path: Path) -> None:
    source = tmp_path / "invalid.txt"
    source.write_bytes(b"\xff")

    with pytest.raises(SourceExtractionError) as error:
        extract_text(source)

    assert "encoding" in str(error.value).lower()
    assert str(source) in str(error.value)


def test_normalizes_line_endings_trailing_whitespace_and_large_gaps(
    tmp_path: Path,
) -> None:
    source = tmp_path / "spacing.markdown"
    source.write_bytes("第一行  \r\n第二行\t\r\n\r\n\r\n\r\n\r\n结局 \r\n".encode())

    assert extract_text(source) == "第一行\n第二行\n\n\n结局"


def test_rejects_unknown_suffix_with_normalized_suffix(tmp_path: Path) -> None:
    source = tmp_path / "novel.RTF"
    source.write_text("内容", encoding="utf-8")

    with pytest.raises(SourceExtractionError, match=r"^unsupported source type: \.rtf$"):
        extract_text(source)


def test_rejects_empty_extracted_text(tmp_path: Path) -> None:
    source = tmp_path / "blank.txt"
    source.write_text(" \t\r\n\r\n", encoding="utf-8")

    with pytest.raises(SourceExtractionError, match="^extracted source is empty$"):
        extract_text(source)


def test_extracts_docx_paragraphs_and_concatenates_inline_runs(tmp_path: Path) -> None:
    source = tmp_path / "novel.docx"
    _write_docx(
        source,
        """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>第一章开端</w:t></w:r></w:p>
    <w:p><w:r><w:t>内联文字</w:t></w:r><w:r><w:t>连接</w:t></w:r></w:p>
    <w:p><w:r><w:t>制表</w:t><w:tab/><w:t>换行</w:t><w:br/><w:t>继续</w:t></w:r></w:p>
    <w:p><w:r><w:t>最终结局</w:t></w:r></w:p>
  </w:body>
</w:document>""",
    )

    result = extract_text(source)

    assert result.splitlines()[0] == "第一章开端"
    assert "内联文字连接" in result
    assert "制表\t换行\n继续" in result
    assert result.endswith("最终结局")


@pytest.mark.parametrize(
    ("document_xml", "name"),
    [(None, "missing.docx"), ("<broken", "malformed.docx")],
)
def test_docx_errors_are_attributed_to_docx(
    tmp_path: Path, document_xml: str | None, name: str
) -> None:
    source = tmp_path / name
    _write_docx(source, document_xml)

    with pytest.raises(SourceExtractionError, match="DOCX"):
        extract_text(source)


def test_extracts_epub_body_documents_in_spine_order(tmp_path: Path) -> None:
    source = tmp_path / "novel.epub"
    _write_epub(source)

    result = extract_text(source)

    assert result.index("第一章") < result.index("最后一章")
    assert "开端&相遇" in result
    assert "线索一" in result
    assert result.endswith("故事结局")
    assert "不应提取的书名" not in result
    assert "也不应出现" not in result


def test_missing_epub_container_is_attributed_to_epub(tmp_path: Path) -> None:
    source = tmp_path / "missing-container.epub"
    _write_epub(source, include_container=False)

    with pytest.raises(SourceExtractionError, match="EPUB"):
        extract_text(source)


def test_missing_epub_spine_document_is_attributed_to_epub(tmp_path: Path) -> None:
    source = tmp_path / "missing-spine-document.epub"
    _write_epub(source, include_second_document=False)

    with pytest.raises(SourceExtractionError, match="EPUB"):
        extract_text(source)


def test_pdf_reports_exact_missing_dependency_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "novel.pdf"
    source.write_bytes(b"%PDF-placeholder")
    real_import = builtins.__import__

    def reject_pypdf(name: str, *args: object, **kwargs: object) -> object:
        if name == "pypdf":
            raise ModuleNotFoundError("No module named 'pypdf'", name="pypdf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_pypdf)

    with pytest.raises(SourceExtractionError, match="^PDF extraction requires pypdf$"):
        extract_text(source)


def test_pdf_reports_blank_pages_need_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scanned.pdf"
    source.write_bytes(b"%PDF-placeholder")

    class BlankPage:
        def extract_text(self) -> str | None:
            return None

    class FakeReader:
        def __init__(self, path: Path) -> None:
            self.pages = [BlankPage(), BlankPage()]

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=FakeReader))

    with pytest.raises(
        SourceExtractionError,
        match="^PDF contains no extractable text; OCR is required$",
    ):
        extract_text(source)


def test_pdf_reader_errors_are_attributed_to_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "corrupt.pdf"
    source.write_bytes(b"not-a-pdf")

    class BrokenReader:
        def __init__(self, path: Path) -> None:
            raise ValueError("bad xref")

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=BrokenReader))

    with pytest.raises(SourceExtractionError, match="PDF"):
        extract_text(source)


def test_cli_refuses_same_input_and_output_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    original = "第一章\n原始内容\n最终结局"
    source.write_text(original, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--output", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "same" in completed.stderr.lower()
    assert source.read_text(encoding="utf-8") == original


def test_cli_writes_utf8_trailing_newline_and_prints_absolute_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "novel.txt"
    output = tmp_path / "nested" / "extracted.txt"
    source.write_text("第一章\n开端\n\n最终章\n结局", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(output.resolve())
    assert output.read_bytes() == "第一章\n开端\n\n最终章\n结局\n".encode("utf-8")
