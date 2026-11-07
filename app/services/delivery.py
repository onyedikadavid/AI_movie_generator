"""How a line is SAID: volume, pitch, pace and voice quality.

The same words mean different things depending on the moment - "I'm stuck" whispered, shouted, or sobbed. The delivery is
worked out from (1) the speaker's emotion, (2) what is physically happening (stuck, hiding, swimming, ...), (3) the
line itself (! ? ... CAPS, words like "help" / "look out") and is used twice: written into the video prompt so the video
model's own voice performs it, and turned into rate / pitch / volume for the pipeline's TTS voices.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# volume: -3 whisper .. 0 normal .. +3 shout.  pitch: -1 low, 0, +1 high.  pace: -1 slow, 0, +1 fast.
_VOL_WORD = {-3: "whispered", -2: "quiet", -1: "low", 0: "", 1: "raised", 2: "loud", 3: "shouting"}
_PITCH_WORD = {-1: "low-pitched", 0: "", 1: "high-pitched"}
_PACE_WORD = {-1: "slow", 0: "", 1: "quick"}

# (pattern, volume, pitch, pace, quality) - first match on the emotion wins.
_MOODS = [
    (r"furious|rage|enraged|livid|outrag|shout|yell|scream|roar", 3, 0, 1, "harsh, angry"),
    (r"angry|anger|fierce|hostile|bitter|snap|irritat|annoy|frustrat", 2, 0, 1, "harsh, sharp"),
    (r"terrified|panic|frantic|alarm|horrif", 2, 1, 1, "trembling, panicked"),
    (r"scared|afraid|fear|anxious|nervous|trembl|worried|uneasy", -1, 1, 1, "shaky, trembling"),
    (r"plead|begg|desperate", 0, 1, 0, "pleading, strained"),
    (r"apolog|sorry|forgiv|remorse|regret|ashamed|guilt|repent", -1, 0, -1, "hesitant, guilty"),
    (r"sad|cry|cries|tear|sorrow|grief|griev|mourn|heartbroken|sob|weep|lonely|defeated|despair", -1, -1, -1, "broken, tearful"),
    (r"excited|thrill|elated|ecstatic|eager", 2, 1, 1, "bright, excited"),
    (r"joy|happy|cheer|laugh|delight|proud|relieved|grateful|hopeful|amus|playful", 1, 1, 1, "warm, cheerful"),
    (r"whisper|hushed|secret|sneak|stealth", -3, 0, -1, "breathy whisper"),
    (r"quiet|soft|gentle|tender|calm|soothing|comfort|kind|loving|sleepy", -2, 0, -1, "soft, gentle"),
    (r"stern|serious|grave|solemn|firm|cold|resolute|authorit|disapprov|severe|stoic|command|determin", 1, -1, -1, "firm, steady"),
    (r"disappoint|tired|exhaust|weary|weak|faint|hurt|pain", -1, -1, -1, "weak, tired"),
    (r"surpris|shock|stunned|astonish|disbelie|startl|gasp|amaz|wonder|awe", 1, 1, 0, "astonished, wide-eyed"),
    (r"curious|puzzl|confus|thought|wonder", 0, 0, -1, "curious, questioning"),
    (r"explain|teach|inform|lectur|narrat", 0, 0, 0, "clear, friendly, well-paced"),
]
_MOODS = [(re.compile(p, re.I), v, pi, pa, q) for p, v, pi, pa, q in _MOODS]

# What is physically happening changes how the voice comes out (applied on top of the emotion).
_SITUATION_VOICE = {
    "stuck": (1, 0, 0, "strained, out of breath, struggling"),
    "lying": (-1, -1, -1, "weak, labored"),
    "swimming": (0, 0, 0, "breathless, gasping between words"),
    "climbing": (0, 0, 0, "strained, panting"),
    "hiding": (-3, 0, -1, "hushed whisper"),
}
_SCENE_CUES = [
    (re.compile(r"\b(whisper|hid(?:e|ing|den)|sneak|tiptoe|secret|quietly)\b", re.I), -3, "hushed whisper"),
    (re.compile(r"\b(shout|yell|scream|roar|bellow|call(?:s|ing)? out)\b", re.I), 3, "loud, carrying"),
    (re.compile(r"\b(far away|across the (?:field|river|valley)|from a distance)\b", re.I), 2, "projected to carry far"),
]
_URGENT = re.compile(r"\b(help|look out|watch out|run|quick|hurry|stop|no+|careful|fire|danger|now)\b", re.I)


@dataclass
class Delivery:
    volume: int = 0
    pitch: int = 0
    pace: int = 0
    quality: str = ""

    def phrase(self) -> str:
        """'in a low, slow, broken, tearful voice' - '' when the delivery is plain (a normal speaking voice)."""
        bits = [_VOL_WORD[self.volume], _PITCH_WORD[self.pitch], _PACE_WORD[self.pace]]
        bits += [q.strip() for q in self.quality.split(",")]
        out = []
        for b in bits:
            if b and b not in out and not any(b in o or o in b for o in out):
                out.append(b)
        out = out[:4]
        return f"in a {', '.join(out)} voice" if out else ""

    def prosody(self) -> tuple:
        """(rate %, pitch Hz, volume %) for the pipeline's TTS voices."""
        return (self.pace * 10, self.pitch * 3 + (-2 if self.volume <= -2 else 0), self.volume * 6)


def analyze(expression: str = "", action: str = "", line: str = "", situation: str = "", scene: str = "") -> Delivery:
    d = Delivery()
    mood = f"{expression or ''}"
    for rx, v, pi, pa, q in _MOODS:
        if rx.search(mood):
            d = Delivery(v, pi, pa, q)
            break
    else:
        for rx, v, pi, pa, q in _MOODS:  # no stated emotion: the action may carry it ("whispers", "clenches fists")
            if (action or "") and rx.search(action):
                d = Delivery(v, pi, pa, q)
                break

    sv = _SITUATION_VOICE.get(situation)
    if sv:
        sv_vol, sv_pitch, sv_pace, sv_q = sv
        # A whisper stays a whisper; otherwise the situation adds its own strain to the emotion.
        if situation == "hiding":
            d.volume = sv_vol
        elif d.volume == 0 and not d.quality:
            d.volume = sv_vol
        d.pitch = d.pitch or sv_pitch
        d.pace = d.pace or sv_pace
        d.quality = f"{d.quality}, {sv_q}" if d.quality else sv_q

    for rx, vol, q in _SCENE_CUES:
        if rx.search(f"{action or ''} {scene or ''}") and d.volume != -3:
            d.volume, d.quality = (vol, f"{d.quality}, {q}" if d.quality and q not in d.quality else (d.quality or q))
            break

    text = (line or "").strip()
    if text:
        letters = [w for w in re.findall(r"[A-Za-z']{3,}", text)]
        caps = sum(1 for w in letters if w.isupper())
        bang = text.count("!")
        if (caps >= 2 or bang >= 2) and d.volume >= 0:
            d.volume = min(3, max(d.volume, 2) + (1 if caps >= 2 else 0))
        elif bang == 1 and -2 < d.volume < 1:
            d.volume += 1
        if re.search(r"\.\.\.|…", text) and d.volume <= 0:
            d.pace = min(d.pace, -1)
            if "hesitant" not in d.quality:
                d.quality = f"{d.quality}, trailing and hesitant" if d.quality else "hesitant, trailing off"
        if text.rstrip('"”').endswith("?") and not d.quality:
            d.quality = "questioning, rising at the end"
        if _URGENT.search(text) and d.volume >= 0 and d.volume < 2 and (bang or d.pace > 0):
            d.volume, d.pace = 2, max(d.pace, 1)
    d.volume = max(-3, min(3, d.volume))
    return d


def delivery_phrase(expression: str = "", action: str = "", line: str = "", situation: str = "", scene: str = "") -> str:
    return analyze(expression, action, line, situation, scene).phrase()
