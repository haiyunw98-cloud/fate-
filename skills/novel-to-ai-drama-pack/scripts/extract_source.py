from __future__ import annotations

import argparse
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree


class SourceExtractionError(RuntimeError):
    pass


def _extract_plain(path: Path) -> str:
    encodings = ("utf-8-sig", "utf-8", "gb18030")
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as error:
            raise SourceExtractionError(
                f"plain source read failed for {path}: {error}"
            ) from error
    raise SourceExtractionError(
        f"encoding failure for {path}: tried {', '.join(encodings)}"
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _extract_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            document = ElementTree.fromstring(archive.read("word/document.xml"))

        paragraphs: list[str] = []
        for paragraph in document.iter():
            if _local_name(paragraph.tag) != "p":
                continue
            content: list[str] = []
            for element in paragraph.iter():
                name = _local_name(element.tag)
                if name == "t" and element.text:
                    content.append(element.text)
                elif name == "tab":
                    content.append("\t")
                elif name in {"br", "cr"}:
                    content.append("\n")
            paragraphs.append("".join(content))
        return "\n".join(paragraphs)
    except (
        OSError,
        zipfile.BadZipFile,
        KeyError,
        ElementTree.ParseError,
        RuntimeError,
        NotImplementedError,
    ) as error:
        raise SourceExtractionError(
            f"DOCX extraction failed for {path}: {error}"
        ) from error


_XHTML_BLOCKS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tr",
    "ul",
}
_XHTML_IGNORED = {"noscript", "script", "style", "template"}


def _xhtml_body_text(data: bytes) -> str:
    document = ElementTree.fromstring(data)
    body = next(
        (element for element in document.iter() if _local_name(element.tag) == "body"),
        None,
    )
    if body is None:
        raise ValueError("XHTML document has no body")

    parts: list[str] = []

    def add_break() -> None:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")

    def add_text(value: str | None) -> None:
        if value is None:
            return
        if "\n" in value and not value.strip():
            return
        parts.append(value)

    def visit(element: ElementTree.Element) -> None:
        name = _local_name(element.tag)
        if name in _XHTML_IGNORED:
            return
        if name == "br":
            add_break()
            return
        if name in _XHTML_BLOCKS:
            add_break()
        add_text(element.text)
        for child in element:
            visit(child)
            add_text(child.tail)
        if name in _XHTML_BLOCKS:
            add_break()

    visit(body)
    return "".join(parts)


def _epub_member_path(opf_path: str, href: str) -> str:
    href_path = unquote(urlsplit(href).path)
    return posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href_path))


def _extract_epub(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            container = ElementTree.fromstring(
                archive.read("META-INF/container.xml")
            )
            rootfile = next(
                (
                    element
                    for element in container.iter()
                    if _local_name(element.tag) == "rootfile"
                    and element.get("full-path")
                ),
                None,
            )
            if rootfile is None:
                raise ValueError("container has no rootfile")

            opf_path = rootfile.get("full-path")
            if opf_path is None:
                raise ValueError("container rootfile has no path")
            package = ElementTree.fromstring(archive.read(opf_path))

            manifest_element = next(
                (
                    element
                    for element in package.iter()
                    if _local_name(element.tag) == "manifest"
                ),
                None,
            )
            if manifest_element is None:
                raise ValueError("package has no manifest")
            manifest = {
                item_id: href
                for item in manifest_element
                if _local_name(item.tag) == "item"
                and (item_id := item.get("id"))
                and (href := item.get("href"))
            }
            if not manifest:
                raise ValueError("package manifest is empty")

            spine_element = next(
                (
                    element
                    for element in package.iter()
                    if _local_name(element.tag) == "spine"
                ),
                None,
            )
            if spine_element is None:
                raise ValueError("package has no spine")
            spine_ids = [
                item.get("idref")
                for item in spine_element
                if _local_name(item.tag) == "itemref"
            ]
            if not spine_ids:
                raise ValueError("package spine is empty")

            documents: list[str] = []
            for item_id in spine_ids:
                if not item_id or item_id not in manifest:
                    raise ValueError(f"spine item is missing from manifest: {item_id}")
                document_path = _epub_member_path(opf_path, manifest[item_id])
                documents.append(_xhtml_body_text(archive.read(document_path)))
        return "\n".join(documents)
    except SourceExtractionError:
        raise
    except (
        OSError,
        zipfile.BadZipFile,
        KeyError,
        ElementTree.ParseError,
        UnicodeError,
        ValueError,
        RuntimeError,
        NotImplementedError,
    ) as error:
        raise SourceExtractionError(
            f"EPUB extraction failed for {path}: {error}"
        ) from error


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as error:
        if error.name == "pypdf":
            raise SourceExtractionError("PDF extraction requires pypdf") from error
        raise SourceExtractionError(f"PDF import failed: {error}") from error
    except ImportError as error:
        raise SourceExtractionError(f"PDF import failed: {error}") from error

    try:
        reader = PdfReader(path)
        page_text = [page.extract_text() for page in reader.pages]
    except Exception as error:
        raise SourceExtractionError(
            f"PDF extraction failed for {path}: {error}"
        ) from error

    if not any(text and text.strip() for text in page_text):
        raise SourceExtractionError(
            "PDF contains no extractable text; OCR is required"
        )
    return "\n".join(text or "" for text in page_text)


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def extract_text(path: Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    handlers: dict[str, Callable[[Path], str]] = {
        ".txt": _extract_plain,
        ".md": _extract_plain,
        ".markdown": _extract_plain,
        ".docx": _extract_docx,
        ".epub": _extract_epub,
        ".pdf": _extract_pdf,
    }
    try:
        handler = handlers[suffix]
    except KeyError as error:
        raise SourceExtractionError(f"unsupported source type: {suffix}") from error

    text = _normalize(handler(source))
    if not text:
        raise SourceExtractionError("extracted source is empty")
    return text


def _same_file(input_path: Path, output_path: Path) -> bool:
    if input_path.resolve() == output_path.resolve():
        return True
    if not input_path.exists() or not output_path.exists():
        return False
    try:
        return input_path.samefile(output_path)
    except OSError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract normalized text from a novel")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)

    try:
        if _same_file(arguments.input, arguments.output):
            raise SourceExtractionError("input and output must not be the same path")
        text = extract_text(arguments.input)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(f"{text}\n", encoding="utf-8")
    except SourceExtractionError as error:
        print(error, file=sys.stderr)
        return 1

    print(arguments.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
