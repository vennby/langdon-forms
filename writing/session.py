"""Local repository layout.

    .langdon/config.json
    .langdon/sessions/<id>/meta.json
    .langdon/sessions/<id>/journal.bin     while the sitting is open
    .langdon/sessions/<id>/session.tr     once sealed
    .langdon/sessions/<id>/report.html

A sealed .tr file is immutable.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from writing.codec import (
    JOURNAL_HEADER,
    JOURNAL_MAGIC,
    TOOL_VERSION,
    VERSION,
    Event,
    LangdonError,
    encode_framed,
    read_journal,
    read_trace,
    write_trace,
)

TRACE_FILENAME = "session.tr"
_SESSION_ID = re.compile(r"[0-9a-f]{16}")

REPO_DIR = ".langdon"


def init_repo(start: Path | None = None) -> Path:
    root = (start or Path.cwd()).resolve()
    repo = root / REPO_DIR
    if (repo / "config.json").is_file():
        return root
    (repo / "sessions").mkdir(parents=True, exist_ok=True)
    _write_json(
        repo / "config.json",
        {"version": VERSION, "tool_version": TOOL_VERSION, "created_unix_ms": _now_ms()},
    )
    return root


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_write(path, data)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    # Windows denies the replace while a scanner or indexer still has the file open.
    delay = 0.02
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


@dataclass
class Session:
    root: Path
    directory: Path
    meta: dict
    last_t: int = 0
    _clock_ready: bool = field(default=False, repr=False)

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def sealed(self) -> bool:
        return self.trace_path.is_file()

    @property
    def label(self) -> str:
        return self.meta.get("label") or "untitled"

    @property
    def journal_path(self) -> Path:
        return self.directory / "journal.bin"

    @property
    def trace_path(self) -> Path:
        return self.directory / TRACE_FILENAME

    def save_meta(self) -> None:
        stored = {key: value for key, value in self.meta.items() if not key.startswith("_")}
        _write_json(self.directory / "meta.json", stored)

    def ensure_clock(self) -> None:
        if self._clock_ready:
            return
        if self.journal_path.is_file():
            events, _truncated = read_journal(self.journal_path.read_bytes())
            self.last_t = events[-1].t if events else 0
        else:
            self.last_t = 0
        self._clock_ready = True

    def load(self) -> tuple[dict, list[Event], bool]:
        """Return header, events, and whether an open journal was truncated."""
        if self.trace_path.is_file():
            header, events = read_trace(self.trace_path.read_bytes())
            return header, events, False
        if not self.journal_path.is_file():
            return self._open_header([]), [], False
        events, truncated = read_journal(self.journal_path.read_bytes())
        header = self._open_header(events)
        header["byte_size"] = self.journal_path.stat().st_size
        return header, events, truncated

    def _open_header(self, events: list[Event]) -> dict:
        return {
            "v": VERSION,
            "path": self.label,
            "tool": "langdon",
            "tool_version": self.meta.get("tool_version", TOOL_VERSION),
            "event_count": len(events),
            "synthetic": bool(self.meta.get("synthetic")),
            "created_unix_ms": self.meta.get("created_unix_ms", 0),
            "id": self.id,
            "sha256": None,
            "byte_size": 0,
        }

    def append(self, events: list[Event]) -> None:
        if self.sealed:
            raise LangdonError(f"session {self.id} is sealed and cannot be changed")
        if not events:
            return
        self.ensure_clock()
        ordered: list[Event] = []
        for event in events:
            if event.t < self.last_t:
                event = Event(self.last_t, event.op, event.pos, event.text, event.anchor)
            ordered.append(event)
        if not self.journal_path.is_file():
            self.journal_path.write_bytes(JOURNAL_HEADER.pack(JOURNAL_MAGIC, VERSION))
        framed, self.last_t = encode_framed(ordered, self.last_t)
        with open(self.journal_path, "ab") as handle:
            handle.write(framed)
            handle.flush()
            os.fsync(handle.fileno())
        self._clock_ready = True
        self.meta["event_count"] = int(self.meta.get("event_count") or 0) + len(ordered)
        self.meta["last_append_unix_ms"] = _now_ms()
        self.meta["last_t"] = self.last_t
        self.save_meta()

    def seal(self, final_text: str | None = None) -> Path:
        if self.sealed:
            return self.trace_path
        _header, events, truncated = self.load() if self.journal_path.is_file() else ({}, [], False)
        if truncated:
            raise LangdonError("the journal ends in a partial event; it was not sealed")
        if final_text is not None:
            closing_t = events[-1].t if events else 0
            if not events or events[-1].op != "checkpoint" or events[-1].text != final_text:
                events = [*events, Event(closing_t, "checkpoint", text=final_text)]
        data = write_trace(
            session_id=bytes.fromhex(self.id),
            created_unix_ms=int(self.meta["created_unix_ms"]),
            path=self.label,
            events=events,
            synthetic=bool(self.meta.get("synthetic")),
        )
        _atomic_write(self.trace_path, data)
        if self.journal_path.is_file():
            self.journal_path.unlink()
        self.meta["sealed"] = True
        self.meta["event_count"] = len(events)
        self.meta["sha256"] = read_trace(data)[0]["sha256"]
        self.meta["byte_size"] = len(data)
        self.save_meta()
        return self.trace_path


def create_session(
    root: Path,
    *,
    label: str,
    doc_path: Path | None = None,
    synthetic: bool = False,
    baseline: str = "",
) -> Session:
    session_id = secrets.token_bytes(8)
    directory = root / REPO_DIR / "sessions" / session_id.hex()
    directory.mkdir(parents=True, exist_ok=False)
    meta = {
        "id": session_id.hex(),
        "label": label,
        "doc_path": str(doc_path) if doc_path else None,
        "created_unix_ms": _now_ms(),
        "tool_version": TOOL_VERSION,
        "sealed": False,
        "synthetic": synthetic,
        "event_count": 0,
        "token": secrets.token_hex(16),
        "last_append_unix_ms": _now_ms(),
        "last_t": 0,
    }
    session = Session(root=root, directory=directory, meta=meta, last_t=0, _clock_ready=True)
    session.save_meta()
    if baseline:
        session.append([Event(0, "checkpoint", text=baseline)])
    return session


def open_session(root: Path, session_id: str) -> Session:
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise LangdonError("not found")
    directory = (root / REPO_DIR / "sessions" / session_id).resolve()
    sessions = (root / REPO_DIR / "sessions").resolve()
    if directory.parent != sessions:
        raise LangdonError("not found")
    meta_path = directory / "meta.json"
    if not meta_path.is_file():
        raise LangdonError("not found")
    return Session(root=root, directory=directory, meta=_read_json(meta_path))


def jsonl_size(events: list[Event]) -> int:
    """A plain JSONL encoding, used only to show what the binary trace saves."""
    total = 0
    for event in events:
        total += len(json.dumps(event.to_json(), ensure_ascii=False).encode("utf-8")) + 1
    return total
