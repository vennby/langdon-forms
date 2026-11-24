"""Render a sealed or open trace as a self-contained HTML report."""

from __future__ import annotations

import json
from pathlib import Path

from writing.codec import Event
from writing.features import analyze
from writing.session import jsonl_size


def _embed(payload: dict) -> str:
    return (
        json.dumps(payload, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_report(header: dict, events: list[Event], analysis: dict, truncated: bool = False) -> str:
    payload = {
        "id": header.get("id"),
        "path": header.get("path") or "untitled",
        "created_unix_ms": header.get("created_unix_ms") or 0,
        "synthetic": bool(header.get("synthetic")),
        "sha256": header.get("sha256"),
        "trace_bytes": header.get("byte_size") or 0,
        "jsonl_bytes": jsonl_size(events),
        "truncated": truncated,
        "analysis": analysis,
        "events": [event.to_json() for event in events],
    }
    template = Path(__file__).with_name("report.html").read_text(encoding="utf-8")
    return template.replace("__LANGDON_DATA__", _embed(payload))


def publish(session) -> Path:
    header, events, truncated = session.load()
    path = session.directory / "report.html"
    path.write_text(
        render_report(header, events, analyze(events), truncated),
        encoding="utf-8",
    )
    return path
