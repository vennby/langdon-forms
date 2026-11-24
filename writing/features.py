"""Process features extracted from a writing trace.

The numbers describe how characters and time entered the session. They are a
provenance summary and a stable vector for later study. They are not a judgment
that a model did or did not author the ideas in the text.
"""

from __future__ import annotations

import statistics
from typing import Any

from writing.codec import Event
from writing.replay import Replay, integrity

FEATURE_VERSION = 1

# Gaps between successive single-character keystrokes at or under this length
# count as typing time. Longer gaps are long pauses. This cap is the usual
# window in typing research for "still in the run."
TYPING_GAP_MS = 2000
HESITATION_MS = 1000
# A single insertion at least this long was not typed one character at a time.
# Shorter multi-character insertions still happen from input methods.
BLOCK_CHARS = 80
BOUNDARY = set(".?!,\n;:")


def _is_single_key(event: Event) -> bool:
    return event.op in ("insert", "delete") and len(event.text) == 1


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return int(round(ordered[low] + (ordered[high] - ordered[low]) * (position - low)))


def analyze(events: list[Event]) -> dict[str, Any]:
    replay = Replay()
    prev_key_t: int | None = None
    boundary_char = ""
    blur_t: int | None = None

    keystroke_chars = 0
    composed_chars = 0
    deleted_chars = 0
    pasted_chars = 0
    paste_events = 0
    block_insert_chars = 0
    history_insert_chars = 0
    history_delete_chars = 0
    block_events = 0
    max_event_chars = 0
    gaps: list[int] = []
    away_ms = 0
    boundary_pauses = 0

    for event in events:
        if _is_single_key(event) and prev_key_t is not None:
            gap = event.t - prev_key_t
            if gap > 0:
                gaps.append(gap)
                if gap > HESITATION_MS and boundary_char in BOUNDARY:
                    boundary_pauses += 1
        if event.op == "blur":
            blur_t = event.t
        elif event.op == "focus" and blur_t is not None:
            away_ms += max(0, event.t - blur_t)
            blur_t = None

        replay.apply(event)

        if event.op == "insert":
            length = len(event.text)
            if length >= BLOCK_CHARS:
                block_insert_chars += length
                block_events += 1
            elif length == 1:
                keystroke_chars += 1
            else:
                composed_chars += length
            max_event_chars = max(max_event_chars, length)
        elif event.op == "delete":
            deleted_chars += len(event.text)
        elif event.op == "paste":
            pasted_chars += len(event.text)
            paste_events += 1
            if len(event.text) >= BLOCK_CHARS:
                block_events += 1
            max_event_chars = max(max_event_chars, len(event.text))
        elif event.op == "history_insert":
            history_insert_chars += len(event.text)
            max_event_chars = max(max_event_chars, len(event.text))
        elif event.op == "history_delete":
            history_delete_chars += len(event.text)

        if _is_single_key(event):
            prev_key_t = event.t
            boundary_char = replay.text[replay.cursor - 1] if replay.cursor > 0 else ""
        elif event.op in ("insert", "paste", "history_insert", "checkpoint"):
            prev_key_t = None
            boundary_char = ""

    if blur_t is not None and events:
        away_ms += max(0, events[-1].t - blur_t)

    iki = [gap for gap in gaps if gap <= TYPING_GAP_MS]
    long_gaps = [gap for gap in gaps if gap > TYPING_GAP_MS]
    hesitations = [gap for gap in gaps if HESITATION_MS < gap <= TYPING_GAP_MS]
    active_ms = sum(iki)
    produced = keystroke_chars + composed_chars + pasted_chars + block_insert_chars + history_insert_chars
    median = _percentile(iki, 0.5)
    mean = statistics.fmean(iki) if iki else 0
    cv = None
    if len(iki) >= 2 and mean > 0:
        cv = round(statistics.stdev(iki) / mean, 3)
    typing_wpm = None
    if active_ms > 0 and keystroke_chars > 0:
        typing_wpm = round((keystroke_chars / 5) / (active_ms / 60_000), 1)

    edges = [0, 50, 100, 150, 200, 300, 500, 800, 1200, 2000]
    histogram = [0] * (len(edges) - 1)
    for gap in iki:
        for index in range(len(edges) - 1):
            last_bucket = index == len(edges) - 2
            if edges[index] <= gap < edges[index + 1] or (last_bucket and gap == edges[index + 1]):
                histogram[index] += 1
                break

    check = integrity(events)
    metrics: dict[str, Any] = {
        "feature_version": FEATURE_VERSION,
        "session_ms": events[-1].t if events else 0,
        "typing_ms": active_ms,
        "long_pause_ms": sum(long_gaps),
        "away_ms": away_ms,
        "hesitation_count": len(hesitations),
        "long_pause_count": len(long_gaps),
        "boundary_pause_count": boundary_pauses,
        "keystroke_chars": keystroke_chars,
        "composed_chars": composed_chars,
        "deleted_chars": deleted_chars,
        "pasted_chars": pasted_chars,
        "paste_events": paste_events,
        "block_insert_chars": block_insert_chars,
        "history_insert_chars": history_insert_chars,
        "history_delete_chars": history_delete_chars,
        "block_events": block_events,
        "max_event_chars": max_event_chars,
        "iki_samples": len(iki),
        "median_iki_ms": median,
        "iki_p10_ms": _percentile(iki, 0.10),
        "iki_p90_ms": _percentile(iki, 0.90),
        "iki_cv": cv,
        "iki_histogram": histogram,
        "iki_histogram_edges": edges,
        "typing_wpm": typing_wpm,
        "revision_ratio": round(deleted_chars / keystroke_chars, 4) if keystroke_chars else 0,
        "paste_ratio": round(pasted_chars / produced, 4) if produced else 0,
        "produced_chars": produced,
        "replay_mismatches": check["replay_mismatches"],
        "checkpoint_snaps": replay.checkpoint_snaps,
        "final_matches": check["final_matches"],
    }
    return reading(metrics)


def reading(metrics: dict[str, Any]) -> dict[str, Any]:
    produced = metrics["produced_chars"]
    paste_ratio = metrics["paste_ratio"]
    median = metrics["median_iki_ms"]
    reasons: list[str] = []
    nontyped = metrics["pasted_chars"] + metrics["block_insert_chars"]
    nontyped_ratio = nontyped / produced if produced else 0

    if produced == 0 and metrics["deleted_chars"] == 0:
        label, title, summary = (
            "empty",
            "Empty session",
            "This session records no text changes.",
        )
    elif nontyped >= 80 and nontyped_ratio >= 0.5 and metrics["keystroke_chars"] <= nontyped:
        label, title = "inserted", "Block insertion"
        summary = "Most of the new text arrived in blocks, one event at a time, rather than as separate keystrokes."
        share = round(nontyped_ratio * 100)
        reasons.append(
            f"{share}% of the new characters arrived by paste or a single large insertion "
            f"({metrics['pasted_chars']} pasted, {metrics['block_insert_chars']} in a block)."
        )
        if metrics["max_event_chars"] >= BLOCK_CHARS:
            reasons.append(f"The largest single insertion is {metrics['max_event_chars']} characters.")
    elif metrics["block_events"] or paste_ratio >= 0.15 or metrics["pasted_chars"] >= 40:
        label, title = "mixed", "Mixed entry"
        summary = "Part of the text was typed, and part arrived as a paste or another block insertion."
        reasons.append(
            f"Typed characters: {metrics['keystroke_chars']}. "
            f"Pasted: {metrics['pasted_chars']}. "
            f"Block insertions: {metrics['block_insert_chars']}."
        )
    elif (
        metrics["iki_samples"] >= 12
        and median is not None
        and 40 <= median <= 1000
        and metrics["keystroke_chars"] >= 12
    ):
        label, title = "incremental", "Incremental composition"
        summary = "The characters in this session arrived as separate keystrokes, with a typing cadence and room for pauses."
        reasons.append(f"The median interval between keystrokes is {median} ms, inside the ordinary typing range.")
        if metrics["long_pause_count"]:
            reasons.append(
                f"{metrics['long_pause_count']} pause{'s' if metrics['long_pause_count'] != 1 else ''} "
                f"last longer than 2 seconds."
            )
        if metrics["revision_ratio"] >= 0.03:
            reasons.append(
                f"Deletions remove {round(metrics['revision_ratio'] * 100)}% as many characters as were typed."
            )
        if metrics["boundary_pause_count"]:
            reasons.append(
                f"{metrics['boundary_pause_count']} of the longer gaps follow punctuation or a line break."
            )
    elif metrics["composed_chars"] >= 40 and metrics["keystroke_chars"] < 12 and nontyped < 40:
        label, title = "inconclusive", "Input-method commits"
        summary = "Most characters arrived in short multi-character insertions, the shape an input method leaves when it commits a word."
        reasons.append(f"{metrics['composed_chars']} characters arrived in multi-character insertions shorter than a block.")
    elif metrics["iki_samples"] >= 12 and median is not None and median < 40:
        label, title = "inconclusive", "Timing outside ordinary typing"
        summary = "Keystrokes are present, and the intervals between them are shorter than people usually sustain."
        reasons.append(f"The median interval is {median} ms.")
    else:
        label, title = "inconclusive", "Not enough process"
        summary = "This session does not contain enough ordinary keystrokes to describe how the text was composed."
        reasons.append(f"Single-character keystroke intervals recorded: {metrics['iki_samples']}.")

    if metrics["history_insert_chars"] >= 40 and produced and metrics["history_insert_chars"] >= produced * 0.5:
        reasons.append(
            f"{metrics['history_insert_chars']} characters returned through undo or redo rather than being retyped."
        )

    caveat = (
        "This reading describes the mechanical process: the order characters entered, "
        "and the time that sat between them. Hand-typed text can still have been read from a model. "
        "Software that controls the recorder can also synthesize a trace. "
        "A sealed session is the record of one sitting."
    )
    return {
        "label": label,
        "title": title,
        "summary": summary,
        "reasons": reasons,
        "caveat": caveat,
        "metrics": metrics,
    }
