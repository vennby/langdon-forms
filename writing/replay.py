"""Fold a trace back into document states.

Visual replay applies every event, including checkpoints, which are snapshots
the recorder observed. Integrity replay ignores checkpoints that arrive after
editing has begun and checks that the edits alone reproduce the final snapshot.
"""

from __future__ import annotations

from writing.codec import TEXT_OPS, Event


class Replay:
    def __init__(self) -> None:
        self.text = ""
        self.cursor = 0
        self.anchor = 0
        self.mismatches = 0
        self.checkpoint_snaps = 0
        self.edits = 0

    def apply(self, event: Event) -> None:
        if event.op == "checkpoint":
            if self.edits and self.text != event.text:
                self.checkpoint_snaps += 1
            self.text = event.text
            self.cursor = len(event.text)
            self.anchor = self.cursor
            return
        if event.op == "cursor":
            self.cursor = min(event.pos, len(self.text))
            self.anchor = min(event.anchor, len(self.text))
            return
        if event.op in ("focus", "blur"):
            return
        if event.op in ("delete", "history_delete"):
            self.edits += 1
            start = event.pos
            removed = event.text
            end = start + len(removed)
            if start < 0 or end > len(self.text) or self.text[start:end] != removed:
                self.mismatches += 1
                return
            self.text = self.text[:start] + self.text[end:]
            self.cursor = start
            self.anchor = start
            return
        if event.op in ("insert", "paste", "history_insert"):
            self.edits += 1
            if event.pos < 0 or event.pos > len(self.text):
                self.mismatches += 1
                return
            self.text = self.text[: event.pos] + event.text + self.text[event.pos :]
            self.cursor = event.pos + len(event.text)
            self.anchor = self.cursor
            return
        self.mismatches += 1


def integrity(events: list[Event]) -> dict:
    """Rebuild the document from the baseline and the edits, then compare.

    Checkpoints after the first edit are observations, not inputs. The last of
    them is the document the recorder claimed to have at the end.
    """
    replay = Replay()
    seen_edit = False
    last_checkpoint: str | None = None
    for event in events:
        if event.op == "checkpoint":
            if not seen_edit:
                replay.apply(event)
            else:
                last_checkpoint = event.text
            continue
        if event.op in TEXT_OPS:
            seen_edit = True
            replay.apply(event)
    matches = last_checkpoint is None or last_checkpoint == replay.text
    return {
        "final_text": last_checkpoint if last_checkpoint is not None else replay.text,
        "replay_text": replay.text,
        "final_matches": matches,
        "replay_mismatches": replay.mismatches,
    }
