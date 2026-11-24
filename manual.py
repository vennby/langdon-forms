"""Docs pages read from markdown files in the docs folder."""

from __future__ import annotations

import html
import re
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent / "docs"

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_EM = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_ORDERED = re.compile(r"^\d+\.\s+")


def load_pages() -> list[dict[str, str]]:
    if not DOCS_DIR.is_dir():
        return []
    pages = []
    seen: set[str] = set()
    for path in sorted(DOCS_DIR.glob("*.md")):
        source = path.read_text(encoding="utf-8")
        slug = _slug(path.stem, seen)
        seen.add(slug)
        pages.append({
            "id": slug,
            "title": _title(source) or slug,
            "html": render_markdown(source),
        })
    return pages


def render_markdown(source: str) -> str:
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith("```"):
            chunk: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                chunk.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append("<pre><code>" + html.escape("\n".join(chunk)) + "</code></pre>")
            continue
        marks = len(line) - len(line.lstrip("#"))
        if 1 <= marks <= 6 and line[marks:marks + 1] == " ":
            level = min(marks, 3)
            blocks.append(f"<h{level}>{_inline(line[marks + 1:].strip())}</h{level}>")
            index += 1
            continue
        if line.lstrip().startswith(("- ", "* ")):
            items = []
            while index < len(lines) and lines[index].lstrip().startswith(("- ", "* ")):
                items.append(_inline(lines[index].lstrip()[2:].strip()))
                index += 1
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            continue
        if _ORDERED.match(line.lstrip()):
            items = []
            while index < len(lines) and _ORDERED.match(lines[index].lstrip()):
                items.append(_inline(_ORDERED.sub("", lines[index].lstrip(), count=1).strip()))
                index += 1
            blocks.append("<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>")
            continue
        if line.startswith(">"):
            quote = []
            while index < len(lines) and lines[index].startswith(">"):
                quote.append(lines[index][1:].strip())
                index += 1
            blocks.append(f"<blockquote><p>{_inline(' '.join(quote))}</p></blockquote>")
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip() and not _starts_block(lines[index]):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return "\n".join(blocks)


def _title(source: str) -> str:
    for line in source.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _slug(stem: str, seen: set[str]) -> str:
    stem = re.sub(r"^\d+-", "", stem)
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "page"
    slug = base
    number = 2
    while slug in seen:
        slug = f"{base}-{number}"
        number += 1
    return slug


def _starts_block(line: str) -> bool:
    stripped = line.lstrip()
    if line.startswith("```") or line.startswith(">"):
        return True
    if stripped.startswith(("- ", "* ")):
        return True
    if _ORDERED.match(stripped):
        return True
    marks = len(line) - len(line.lstrip("#"))
    return 1 <= marks <= 6 and line[marks:marks + 1] == " "


def _inline(text: str) -> str:
    safe = html.escape(text, quote=True)
    safe = _CODE.sub(lambda match: "<code>" + match.group(1) + "</code>", safe)

    def link(match: re.Match[str]) -> str:
        label = match.group(1)
        url = html.unescape(match.group(2)).strip()
        if not _safe_url(url):
            return label
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    safe = _LINK.sub(link, safe)
    safe = _BOLD.sub(r"<strong>\1</strong>", safe)
    safe = _EM.sub(r"<em>\1</em>", safe)
    return safe


def _safe_url(url: str) -> bool:
    lowered = url.lower()
    if lowered.startswith(("http://", "https://", "mailto:")):
        return True
    if url.startswith("/") and not url.startswith("//"):
        return True
    return url.startswith("#")
