"""Binary codec for the .tr trace format and the WLOG journal.

Sealed file, little-endian preamble of 68 bytes:

    offset  size  field
    0       4     magic b"TRAC"
    4       2     version (1)
    6       2     flags (bit 0: payload is zlib-compressed)
    8       8     created unix time, milliseconds
    16      8     session id
    24      4     UTF-8 JSON header length
    28      4     uncompressed payload length
    32      4     stored payload length
    36      32    SHA-256 of the uncompressed payload

The header is JSON. The payload is a sequence of frames. Each frame is an
unsigned LEB128 length followed by one event:

    u8 opcode
    uvarint dt_ms          milliseconds since the previous event (0 before the first)

    insert, delete, paste, history_insert, history_delete:
        uvarint code-point position, uvarint utf-8 length, utf-8 text
    cursor:
        uvarint position, uvarint anchor
    focus, blur:
        no payload
    checkpoint:
        uvarint utf-8 length, utf-8 text

Positions are Unicode code point indexes. A journal (WLOG) is the same frames
with a 6-byte header and no compression, so a recorder can append during a sitting.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import dataclass

TOOL_VERSION = "0.1.0"

MAGIC = b"TRAC"
SUFFIX = ".tr"
JOURNAL_MAGIC = b"WLOG"
VERSION = 1
FLAG_ZLIB = 1
PREAMBLE = struct.Struct("<4sHHQ8sIII32s")
JOURNAL_HEADER = struct.Struct("<4sH")

OP_CODE = {
    "insert": 1,
    "delete": 2,
    "paste": 3,
    "cursor": 4,
    "focus": 5,
    "blur": 6,
    "checkpoint": 7,
    "history_insert": 8,
    "history_delete": 9,
}
CODE_OP = {code: name for name, code in OP_CODE.items()}

TEXT_OPS = frozenset({"insert", "delete", "paste", "history_insert", "history_delete"})


class LangdonError(Exception):
    """User-facing failure: bad input, a missing repo, a damaged trace."""


class FormatError(LangdonError):
    """The bytes are not a readable trace."""


class Truncated(FormatError):
    """A length prefix runs past the end of the buffer."""


@dataclass(frozen=True)
class Event:
    t: int
    op: str
    pos: int = 0
    text: str = ""
    anchor: int = 0

    def to_json(self) -> dict:
        item = {"t": self.t, "op": self.op}
        if self.op == "cursor":
            item["pos"] = self.pos
            item["anchor"] = self.anchor
        elif self.op in TEXT_OPS or self.op == "checkpoint":
            item["pos"] = self.pos
            item["text"] = self.text
        return item


def parse_event(raw: dict) -> Event:
    if not isinstance(raw, dict):
        raise LangdonError("an event must be an object")
    op = raw.get("op")
    if op not in OP_CODE:
        raise LangdonError(f"unknown event op {op!r}")
    t_raw = raw.get("t")
    if isinstance(t_raw, bool) or not isinstance(t_raw, (int, float)):
        raise LangdonError("event time must be a number")
    t = int(round(t_raw))
    if t < 0 or t > 10**13:
        raise LangdonError("event time is out of range")
    text = raw.get("text", "")
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise LangdonError("event text must be a string")
    if len(text) > 1_000_000:
        raise LangdonError("event text is too large")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LangdonError("event text is not valid unicode") from exc
    try:
        pos = 0 if raw.get("pos") is None else int(raw.get("pos"))
        anchor_raw = raw.get("anchor")
        anchor = pos if anchor_raw is None else int(anchor_raw)
    except (TypeError, ValueError) as exc:
        raise LangdonError("event position must be an integer") from exc
    if pos < 0 or anchor < 0 or pos > 50_000_000 or anchor > 50_000_000:
        raise LangdonError("event position is out of range")
    if op in TEXT_OPS and text == "":
        raise LangdonError(f"{op} requires text")
    if op != "cursor":
        anchor = 0
    if op in ("focus", "blur"):
        pos = 0
        text = ""
    if op == "checkpoint":
        pos = 0
    return Event(t=t, op=op, pos=pos, text=text, anchor=anchor)


def write_uvarint(buf: bytearray, number: int) -> None:
    if number < 0:
        raise FormatError("a varint cannot be negative")
    while number >= 0x80:
        buf.append((number & 0x7F) | 0x80)
        number >>= 7
    buf.append(number & 0x7F)


def read_uvarint(data: bytes, index: int) -> tuple[int, int]:
    shift = 0
    number = 0
    start = index
    while True:
        if index >= len(data):
            raise Truncated("truncated varint")
        byte = data[index]
        index += 1
        number |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return number, index
        shift += 7
        if index - start > 10:
            raise FormatError("varint is too long")


def _read_text(data: bytes, index: int) -> tuple[str, int]:
    size, index = read_uvarint(data, index)
    if size > len(data) - index:
        raise Truncated("truncated text")
    raw = data[index : index + size]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FormatError("event text is not utf-8") from exc
    return text, index + size


def encode_event(event: Event, last_t: int) -> tuple[bytes, int]:
    if event.op not in OP_CODE:
        raise FormatError(f"unknown event op {event.op!r}")
    t = event.t if event.t >= last_t else last_t
    buf = bytearray()
    buf.append(OP_CODE[event.op])
    write_uvarint(buf, t - last_t)
    if event.op in TEXT_OPS:
        raw = event.text.encode("utf-8")
        write_uvarint(buf, event.pos)
        write_uvarint(buf, len(raw))
        buf.extend(raw)
    elif event.op == "cursor":
        write_uvarint(buf, event.pos)
        write_uvarint(buf, event.anchor)
    elif event.op == "checkpoint":
        raw = event.text.encode("utf-8")
        write_uvarint(buf, len(raw))
        buf.extend(raw)
    return bytes(buf), t


def decode_event(payload: bytes, last_t: int) -> tuple[Event, int]:
    if not payload:
        raise FormatError("empty event")
    op = CODE_OP.get(payload[0])
    if op is None:
        raise FormatError(f"unknown opcode {payload[0]}")
    dt, index = read_uvarint(payload, 1)
    t = last_t + dt
    pos = 0
    text = ""
    anchor = 0
    if op in TEXT_OPS:
        pos, index = read_uvarint(payload, index)
        text, index = _read_text(payload, index)
    elif op == "cursor":
        pos, index = read_uvarint(payload, index)
        anchor, index = read_uvarint(payload, index)
    elif op == "checkpoint":
        text, index = _read_text(payload, index)
    if index != len(payload):
        raise FormatError("event has trailing bytes")
    return Event(t=t, op=op, pos=pos, text=text, anchor=anchor), t


def encode_framed(events: list[Event], last_t: int = 0) -> tuple[bytes, int]:
    buf = bytearray()
    for event in events:
        payload, last_t = encode_event(event, last_t)
        write_uvarint(buf, len(payload))
        buf.extend(payload)
    return bytes(buf), last_t


def decode_framed(data: bytes) -> tuple[list[Event], bool]:
    """Return events and whether a trailing frame was cut off."""
    events: list[Event] = []
    index = 0
    last_t = 0
    truncated = False
    while index < len(data):
        try:
            size, start = read_uvarint(data, index)
        except Truncated:
            truncated = True
            break
        if start + size > len(data):
            truncated = True
            break
        event, last_t = decode_event(data[start : start + size], last_t)
        events.append(event)
        index = start + size
    return events, truncated


def header_json(
    *,
    path: str,
    event_count: int,
    synthetic: bool = False,
) -> bytes:
    payload = {
        "v": VERSION,
        "path": path,
        "tool": "langdon",
        "tool_version": TOOL_VERSION,
        "event_count": event_count,
        "synthetic": bool(synthetic),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_trace(
    *,
    session_id: bytes,
    created_unix_ms: int,
    path: str,
    events: list[Event],
    synthetic: bool = False,
) -> bytes:
    if len(session_id) != 8:
        raise FormatError("session id must be 8 bytes")
    raw_events, _ = encode_framed(events)
    digest = hashlib.sha256(raw_events).digest()
    header = header_json(path=path, event_count=len(events), synthetic=synthetic)
    stored = zlib.compress(raw_events, 9)
    preamble = PREAMBLE.pack(
        MAGIC,
        VERSION,
        FLAG_ZLIB,
        created_unix_ms,
        session_id,
        len(header),
        len(raw_events),
        len(stored),
        digest,
    )
    return preamble + header + stored


def read_trace(data: bytes) -> tuple[dict, list[Event]]:
    if len(data) < PREAMBLE.size:
        raise FormatError("file is too small to be a trace")
    magic, version, flags, created, session_id, header_len, raw_len, stored_len, digest = PREAMBLE.unpack_from(data)
    if magic != MAGIC:
        raise FormatError("file is not a .tr trace")
    if version != VERSION:
        raise FormatError(f"unsupported trace version {version}")
    start = PREAMBLE.size
    end = start + header_len
    payload_end = end + stored_len
    if payload_end > len(data):
        raise FormatError("trace header points past the end of the file")
    try:
        header = json.loads(data[start:end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormatError("trace header is not json") from exc
    stored = data[end:payload_end]
    if flags & FLAG_ZLIB:
        try:
            raw = zlib.decompress(stored)
        except zlib.error as exc:
            raise FormatError("trace payload does not decompress") from exc
    else:
        raw = stored
    if len(raw) != raw_len:
        raise FormatError("trace payload length does not match the header")
    if hashlib.sha256(raw).digest() != digest:
        raise FormatError("trace hash does not match the payload")
    events, truncated = decode_framed(raw)
    if truncated:
        raise FormatError("sealed trace ends in a partial event")
    if isinstance(header, dict) and header.get("event_count") not in (None, len(events)):
        raise FormatError("trace event count does not match the payload")
    header = dict(header)
    header["created_unix_ms"] = created
    header["id"] = session_id.hex()
    header["sha256"] = digest.hex()
    header["byte_size"] = len(data)
    return header, events


def read_journal(data: bytes) -> tuple[list[Event], bool]:
    if len(data) < JOURNAL_HEADER.size:
        raise FormatError("journal is too small")
    magic, version = JOURNAL_HEADER.unpack_from(data)
    if magic != JOURNAL_MAGIC:
        raise FormatError("file is not a langdon journal")
    if version != VERSION:
        raise FormatError(f"unsupported journal version {version}")
    return decode_framed(data[JOURNAL_HEADER.size :])
