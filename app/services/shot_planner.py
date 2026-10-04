"""
Turns a scene's spoken lines into (a) one audio timeline and (b) a list of
shots - the pieces of picture that will each be generated as one short video
clip.

Pure Python, no I/O, so it is easy to test. The key ideas:

* The scene's length is decided by what is SAID (narration + every character's
  lines + natural pauses), not by a guessed `duration_seconds` - so speech is
  never cut off and never followed by dead air.
* The scene is cut into shots of at most `max_len` seconds, preferably at the
  moment the speaker changes, like a real film edit. Short clips are far
  sharper than long stretched ones with this class of video model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Segment:
    index: int
    speaker: str                 # character name, or "Narrator"
    kind: str                    # "narration" | "dialogue" | "ambient"
    text: str = ""
    expression: str = ""
    action: str = ""
    audio_path: str = ""
    duration: float = 0.0        # seconds of actual speech
    start: float = 0.0           # position on the scene timeline
    end: float = 0.0             # start + duration
    gap_after: float = 0.0       # pause that follows this line


@dataclass
class Shot:
    index: int
    start: float
    end: float
    speaker: str
    kind: str
    expression: str = ""
    action: str = ""
    text: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


LEAD_IN = 0.30
SAME_SPEAKER_GAP = 0.25
CHANGE_SPEAKER_GAP = 0.45
TAIL = 0.50


def layout_timeline(
    segments: List[Segment],
    lead_in: float = LEAD_IN,
    same_gap: float = SAME_SPEAKER_GAP,
    change_gap: float = CHANGE_SPEAKER_GAP,
    tail: float = TAIL,
) -> float:
    """Assigns start/end/gap_after to every segment; returns the total length
    in seconds (including the lead-in and the tail)."""
    t = lead_in
    for i, seg in enumerate(segments):
        seg.start = t
        seg.end = t + seg.duration
        if i == len(segments) - 1:
            seg.gap_after = tail
        elif segments[i + 1].speaker == seg.speaker:
            seg.gap_after = same_gap
        else:
            seg.gap_after = change_gap
        t = seg.end + seg.gap_after
    return t


@dataclass
class _Span:
    start: float
    end: float
    seg: Segment


def _spans(segments: List[Segment], total: float, max_len: float) -> List[_Span]:
    """One span per segment (covering its trailing pause too); spans longer
    than max_len are split into equal parts."""
    out: List[_Span] = []
    for i, seg in enumerate(segments):
        s = 0.0 if i == 0 else seg.start
        e = total if i == len(segments) - 1 else segments[i + 1].start
        length = e - s
        parts = max(1, int(math.ceil(length / max_len - 1e-9)))
        step = length / parts
        for k in range(parts):
            out.append(_Span(s + k * step, s + (k + 1) * step if k < parts - 1 else e, seg))
    return out


def _group(spans: List[_Span], max_len: float, min_len: float) -> List[List[_Span]]:
    groups: List[List[_Span]] = []
    for sp in spans:
        if groups and (sp.end - groups[-1][0].start) <= max_len + 1e-6:
            groups[-1].append(sp)
        else:
            groups.append([sp])
    # A sliver at the end isn't worth its own (expensive) video clip.
    if len(groups) > 1 and (groups[-1][-1].end - groups[-1][0].start) < min_len:
        tail = groups.pop()
        groups[-1].extend(tail)
    return groups


def plan_shots(
    segments: List[Segment],
    total: float,
    max_len: float = 4.5,
    min_len: float = 1.6,
    max_shots: int = 5,
    hard_max_len: float = 6.2,
) -> List[Shot]:
    """Partitions [0, total] into consecutive shots (no gaps, no overlaps).

    Shots are at most `max_len` seconds. If that would need more than
    `max_shots` clips, shots are lengthened to fit the budget - but never past
    `hard_max_len` (about what the video model can be stretched to without
    looking slow-motion); a very long scene then simply gets more shots."""
    if not segments:
        raise ValueError("plan_shots needs at least one segment")
    max_len = max(1.0, float(max_len))
    max_shots = max(1, int(max_shots))

    groups = _group(_spans(segments, total, max_len), max_len, min_len)
    # Too many shots for the budget: lengthen them until it fits.
    attempts = 0
    while len(groups) > max_shots and attempts < 12:
        grown = max(max_len * 1.2, total / max_shots * 1.02)
        if grown > hard_max_len:
            max_len = max(max_len, hard_max_len)
            groups = _group(_spans(segments, total, max_len), max_len, min_len)
            break
        max_len = grown
        groups = _group(_spans(segments, total, max_len), max_len, min_len)
        attempts += 1

    shots: List[Shot] = []
    for gi, g in enumerate(groups):
        # The shot's subject is whoever talks the most inside it.
        weight: dict = {}
        order: List[str] = []
        for sp in g:
            key = sp.seg.speaker
            if key not in weight:
                weight[key] = 0.0
                order.append(key)
            weight[key] += sp.end - sp.start
        lead = max(order, key=lambda k: (weight[k], -order.index(k)))
        main = max((sp for sp in g if sp.seg.speaker == lead), key=lambda sp: sp.end - sp.start).seg
        shots.append(
            Shot(
                index=gi,
                start=g[0].start,
                end=g[-1].end,
                speaker=lead,
                kind=main.kind,
                expression=main.expression,
                action=main.action,
                text=main.text,
            )
        )
    return shots
