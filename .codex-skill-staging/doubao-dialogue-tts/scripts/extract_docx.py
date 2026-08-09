#!/usr/bin/env python3
"""Extract visible DOCX paragraphs without requiring python-docx."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def extract_paragraphs(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{{{W_NS}}}p"):
        chunks: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{{{W_NS}}}t":
                chunks.append(node.text or "")
            elif node.tag == f"{{{W_NS}}}tab":
                chunks.append("\t")
            elif node.tag in {f"{{{W_NS}}}br", f"{{{W_NS}}}cr"}:
                chunks.append("\n")
        text = "".join(chunks).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract non-empty DOCX paragraphs.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"input does not exist: {args.input}")
    paragraphs = extract_paragraphs(args.input)
    if args.format == "json":
        rendered = json.dumps(
            {
                "source": str(args.input.resolve()),
                "paragraph_count": len(paragraphs),
                "paragraphs": [
                    {"index": index, "text": text}
                    for index, text in enumerate(paragraphs)
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    else:
        rendered = "\n".join(
            f"[{index:03d}] {text}" for index, text in enumerate(paragraphs)
        )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

