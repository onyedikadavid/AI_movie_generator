"""
Voice casting for the story-to-video pipeline.

Goal: a finished film where the characters speak for THEMSELVES, each with
their own voice (male voices for male characters, female for female), and a
separate narrator voice - not one robotic voice reading everything.

This module is deliberately pure Python (no network, no DB, no TTS engine):
it only decides *who sounds like what*. app/services/tts_service.py does the
actual speech synthesis with the decisions made here.

Voices are Microsoft neural voices (free via the `edge-tts` package). Names
below are edge-tts "short names".
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

NARRATOR_NAME = "Narrator"

# ---------------------------------------------------------------------------
# Voice pools, in the order they are handed out. Each character gets the next
# unused voice of their gender, so two male characters never share a voice.
# ---------------------------------------------------------------------------
_POOLS: Dict[str, Dict[str, List[str]]] = {
    # Nigerian / West-African stories: Nigerian-accented voices first, then
    # other African-English accents, then US/UK voices as extra variety.
    "ng": {
        "male": [
            "en-NG-AbeoNeural", "en-KE-ChilembaNeural", "en-ZA-LukeNeural", "en-TZ-ElimuNeural",
            "en-US-GuyNeural", "en-US-EricNeural", "en-US-RogerNeural", "en-GB-ThomasNeural",
        ],
        "female": [
            "en-NG-EzinneNeural", "en-KE-AsiliaNeural", "en-ZA-LeahNeural", "en-TZ-ImaniNeural",
            "en-US-JennyNeural", "en-US-AriaNeural", "en-US-MichelleNeural", "en-GB-SoniaNeural",
        ],
    },
    "us": {
        "male": [
            "en-US-GuyNeural", "en-US-EricNeural", "en-US-RogerNeural", "en-GB-ThomasNeural",
            "en-NG-AbeoNeural", "en-ZA-LukeNeural", "en-KE-ChilembaNeural", "en-AU-WilliamNeural",
        ],
        "female": [
            "en-US-JennyNeural", "en-US-AriaNeural", "en-US-MichelleNeural", "en-GB-SoniaNeural",
            "en-NG-EzinneNeural", "en-ZA-LeahNeural", "en-AU-NatashaNeural", "en-GB-LibbyNeural",
        ],
    },
    "gb": {
        "male": [
            "en-GB-ThomasNeural", "en-GB-RyanNeural", "en-US-GuyNeural", "en-US-EricNeural",
            "en-IE-ConnorNeural", "en-NG-AbeoNeural", "en-ZA-LukeNeural", "en-AU-WilliamNeural",
        ],
        "female": [
            "en-GB-SoniaNeural", "en-GB-LibbyNeural", "en-US-JennyNeural", "en-US-AriaNeural",
            "en-IE-EmilyNeural", "en-NG-EzinneNeural", "en-ZA-LeahNeural", "en-AU-NatashaNeural",
        ],
    },
}

# The narrator gets a voice that no character is ever given.
_NARRATOR_VOICE = {"ng": "en-GB-RyanNeural", "us": "en-US-ChristopherNeural", "gb": "en-US-ChristopherNeural"}
_RESERVED = set(_NARRATOR_VOICE.values())

# A kid-sounding female voice, used for young girls when available.
_CHILD_FEMALE_VOICE = "en-US-AnaNeural"

# Last-resort voices if a specific voice name ever stops working.
FALLBACK_VOICE = {"male": "en-US-GuyNeural", "female": "en-US-JennyNeural"}

_NG_CUES = re.compile(
    r"\b(nigeria\w*|nollywood|naija|lagos|abuja|igbo|yoruba|hausa|enugu|onitsha|ibadan|"
    r"port harcourt|benin city|calabar|jollof|okada|danfo|agbada|aso ebi|obinna|chinedu|"
    r"emeka|chika|ngozi|adaeze|tunde|segun|femi|ifeanyi|nonso|uche|ekene)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VoiceProfile:
    """Everything the TTS engine needs to speak one line."""

    voice: str            # edge-tts short name, e.g. "en-NG-AbeoNeural"
    gender: str           # "male" | "female"
    rate: int = 0         # speaking-rate change, percent (-25..+20)
    pitch: int = 0        # pitch change, Hz (-12..+12)
    volume: int = 0       # volume change, percent (-20..+20)
    label: str = ""       # who this is, for logs


@dataclass
class CastMember:
    name: str
    gender: str
    age_group: str
    voice_id: str


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------
def detect_region(texts: Iterable[Optional[str]], configured: str = "auto") -> str:
    configured = (configured or "auto").strip().lower()
    if configured in _POOLS:
        return configured
    blob = " ".join(t for t in texts if t)
    return "ng" if _NG_CUES.search(blob) else "us"


# ---------------------------------------------------------------------------
# Gender / age inference (used only when the LLM / user didn't specify)
# ---------------------------------------------------------------------------
_MALE_NAME_WORDS = {
    "father", "dad", "daddy", "papa", "pa", "mr", "mister", "sir", "uncle", "brother", "boy", "son", "husband",
    "grandfather", "grandpa", "king", "prince", "man", "gentleman", "oga", "baba", "bro", "lad",
    "nephew", "groom",
}
_FEMALE_NAME_WORDS = {
    "mother", "mom", "mum", "mummy", "mama", "mommy", "ma", "mrs", "ms", "miss", "madam", "aunt", "auntie", "aunty",
    "sister", "girl", "daughter", "wife", "grandmother", "grandma", "queen", "princess", "woman", "lady",
    "niece", "bride",
}
_HE = re.compile(r"\b(he|him|his|himself)\b", re.IGNORECASE)
_SHE = re.compile(r"\b(she|her|hers|herself)\b", re.IGNORECASE)
_MALE_DESC = re.compile(r"\b(boy|man|male|father|dad|husband|gentleman|son|brother|uncle)\b", re.IGNORECASE)
_FEMALE_DESC = re.compile(r"\b(girl|woman|female|mother|mom|wife|lady|daughter|sister|aunt)\b", re.IGNORECASE)


def infer_gender(name: str, description: str = "", appearance: str = "") -> Optional[str]:
    """Best-effort gender from a character's name/description. None if unclear."""
    tokens = set(re.findall(r"[a-z]+", (name or "").lower()))
    if tokens & _MALE_NAME_WORDS and not tokens & _FEMALE_NAME_WORDS:
        return "male"
    if tokens & _FEMALE_NAME_WORDS and not tokens & _MALE_NAME_WORDS:
        return "female"

    text = f"{description or ''} {appearance or ''}"
    male = len(_HE.findall(text)) + len(_MALE_DESC.findall(text))
    female = len(_SHE.findall(text)) + len(_FEMALE_DESC.findall(text))
    if male > female:
        return "male"
    if female > male:
        return "female"
    return None


def normalize_gender(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip().lower()
    if v in ("male", "m", "man", "boy", "masculine"):
        return "male"
    if v in ("female", "f", "woman", "girl", "feminine"):
        return "female"
    return None


_AGE_NUM = re.compile(r"\b(\d{1,2})[\s-]*(?:year|yr)s?[\s-]*old\b", re.IGNORECASE)


def infer_age_group(name: str, description: str = "", appearance: str = "") -> str:
    text = f"{name or ''} {description or ''} {appearance or ''}".lower()
    m = _AGE_NUM.search(text)
    if m:
        age = int(m.group(1))
        if age <= 12:
            return "child"
        if age <= 19:
            return "teen"
        if age >= 60:
            return "elder"
        return "adult"
    if re.search(r"\b(elderly|elder|old man|old woman|grandfather|grandmother|grandpa|grandma|aged|white[- ]haired|gray[- ]haired|grey[- ]haired|frail)\b", text):
        return "elder"
    if re.search(r"\b(little boy|little girl|child|kid|toddler|young boy|young girl)\b", text):
        return "child"
    if re.search(r"\b(teen|teenage|teenager|adolescent|schoolboy|schoolgirl)\b", text):
        return "teen"
    if re.search(r"\b(boy|girl)\b", text) and not re.search(r"\b(man|woman|elderly|old)\b", text):
        return "teen"
    return "adult"


def normalize_age_group(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip().lower()
    if v in ("child", "kid", "young"):
        return "child"
    if v in ("teen", "teenager", "adolescent"):
        return "teen"
    if v in ("adult", "middle-aged", "middle aged"):
        return "adult"
    if v in ("elder", "elderly", "old", "senior"):
        return "elder"
    return None


def _stable_gender_guess(name: str) -> str:
    """Deterministic male/female pick for a character we know nothing about,
    so the same name always gets the same voice on every re-run."""
    digest = hashlib.sha1((name or "").lower().encode("utf-8")).digest()
    return "male" if digest[0] % 2 == 0 else "female"


# ---------------------------------------------------------------------------
# Casting
# ---------------------------------------------------------------------------
def _pool(region: str, gender: str) -> List[str]:
    reserved = _RESERVED
    return [v for v in _POOLS[region][gender] if v not in reserved]


def cast_characters(characters: Sequence, region: str, narrator_voice: str = "") -> List[CastMember]:
    """
    Decide gender, age group and voice for every character.

    `characters` is any sequence of objects with .name, .description,
    .appearance_prompt, .gender, .age_group and .voice_id attributes (the DB
    Character rows). Existing values are respected (that's how a user's manual
    choice, or an earlier assignment, stays stable); only blanks are filled.
    The returned list is in the same order as the input.
    """
    region = region if region in _POOLS else "us"

    # Voices already taken: the narrator, plus any character that already has one.
    taken = {narrator_voice or _NARRATOR_VOICE[region]}
    for c in characters:
        vid = (getattr(c, "voice_id", None) or "").strip()
        if vid:
            taken.add(vid)

    cast: List[CastMember] = []
    for c in characters:
        name = getattr(c, "name", "") or ""
        desc = getattr(c, "description", "") or ""
        look = getattr(c, "appearance_prompt", "") or ""

        gender = (
            normalize_gender(getattr(c, "gender", None))
            or infer_gender(name, desc, look)
            or _stable_gender_guess(name)
        )
        age = normalize_age_group(getattr(c, "age_group", None)) or infer_age_group(name, desc, look)

        voice = (getattr(c, "voice_id", None) or "").strip()
        if not voice:
            candidates = _pool(region, gender)
            if gender == "female" and age == "child" and _CHILD_FEMALE_VOICE not in taken:
                voice = _CHILD_FEMALE_VOICE
            else:
                voice = next((v for v in candidates if v not in taken), "")
            if not voice:
                # More characters than distinct voices: reuse, but start from a
                # different place in the list per character so it isn't always
                # the same voice repeated.
                idx = int(hashlib.sha1(name.lower().encode("utf-8")).hexdigest(), 16) % len(candidates)
                voice = candidates[idx]
            taken.add(voice)

        cast.append(CastMember(name=name, gender=gender, age_group=age, voice_id=voice))
    return cast


# ---------------------------------------------------------------------------
# Delivery: age + emotion adjust speaking rate / pitch / volume
# ---------------------------------------------------------------------------
_AGE_PROSODY = {
    "child": (4, 8, 0),
    "teen": (2, 4, 0),
    "adult": (0, 0, 0),
    "elder": (-6, -4, -2),
}

# (pattern, (rate %, pitch Hz, volume %)). First match wins.
_EMOTION_PROSODY = [
    (r"angry|anger|furious|rage|enraged|shout|yell|scream|livid|outrag|fierce|hostile|bitter|snap", (6, 1, 12)),
    (r"terrified|scared|afraid|fear|panic|anxious|nervous|trembl|frantic|alarm", (10, 4, 0)),
    (r"plead|begg|beg\b|desperate|apolog|sorry|forgiv|remorse|regret|ashamed|guilt|repent", (-6, 2, -4)),
    (r"sad|cry|cries|tear|sorrow|grief|griev|mourn|heartbroken|sob|weep|lonely|defeated|despair", (-12, -3, -8)),
    (r"joy|happy|excited|cheer|laugh|delight|proud|thrill|relieved|grateful|hopeful|elated", (8, 3, 6)),
    (r"whisper|quiet|soft|gentle|tender|calm|soothing|hushed|low voice", (-8, -2, -15)),
    (r"stern|serious|grave|solemn|firm|cold|disappoint|resolute|authorit|disapprov|severe|stoic", (-6, -2, 0)),
    (r"surpris|shock|stunned|astonish|disbelie|startl|gasp", (6, 4, 4)),
]
_EMOTION_RE = [(re.compile(p, re.IGNORECASE), v) for p, v in _EMOTION_PROSODY]


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def emotion_prosody(expression: str = "", action: str = "") -> tuple:
    text = f"{expression or ''} {action or ''}"
    for rx, vals in _EMOTION_RE:
        if rx.search(text):
            return vals
    return (0, 0, 0)


def profile_for(member: CastMember, expression: str = "", action: str = "") -> VoiceProfile:
    a_rate, a_pitch, a_vol = _AGE_PROSODY.get(member.age_group, (0, 0, 0))
    e_rate, e_pitch, e_vol = emotion_prosody(expression, action)
    return VoiceProfile(
        voice=member.voice_id,
        gender=member.gender,
        rate=_clamp(a_rate + e_rate, -25, 20),
        pitch=_clamp(a_pitch + e_pitch, -12, 12),
        volume=_clamp(a_vol + e_vol, -20, 20),
        label=member.name,
    )


def narrator_profile(region: str, override: str = "", expression: str = "") -> VoiceProfile:
    region = region if region in _POOLS else "us"
    voice = (override or "").strip() or _NARRATOR_VOICE[region]
    e_rate, e_pitch, e_vol = emotion_prosody(expression)
    # A slightly slower, steadier pace reads as "narrator" rather than "character".
    return VoiceProfile(
        voice=voice,
        gender="male",
        rate=_clamp(-4 + e_rate // 2, -25, 20),
        pitch=_clamp(e_pitch // 2, -12, 12),
        volume=_clamp(e_vol // 2, -20, 20),
        label=NARRATOR_NAME,
    )


def is_narrator(speaker: str) -> bool:
    s = (speaker or "").strip().lower()
    return bool(re.fullmatch(r"(the\s+)?(narrator|narration|voice[\s-]?over|v\.?o\.?|storyteller|off[\s-]?screen)", s))


def match_character(speaker: str, members: Sequence[CastMember]) -> Optional[CastMember]:
    """Find the cast member a dialogue line's `speaker` string refers to."""
    s = (speaker or "").strip().lower()
    if not s:
        return None
    for m in members:
        if m.name.strip().lower() == s:
            return m
    for m in members:
        n = m.name.strip().lower()
        if n and (n in s or s in n):
            return m
    s_tokens = set(re.findall(r"[a-z]+", s))
    for m in members:
        if s_tokens & set(re.findall(r"[a-z]+", m.name.lower())):
            return m
    return None


# ---------------------------------------------------------------------------
# Text preparation
# ---------------------------------------------------------------------------
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")


def clean_for_tts(text: str, speaker: str = "") -> str:
    """Strip everything a human wouldn't say aloud: stage directions, speaker
    labels, markdown, emoji. Guarantees terminal punctuation so the voice
    finishes the sentence naturally instead of trailing off."""
    t = text or ""
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|<[^>]*>", " ", t)
    if speaker:
        t = re.sub(rf"^\s*{re.escape(speaker)}\s*:\s*", "", t, flags=re.IGNORECASE)
    t = _EMOJI.sub("", t)
    t = t.replace("&", " and ").replace("\u2026", "...").replace("_", " ").replace("#", " ")
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"\s+", " ", t).strip().strip('"').strip()
    if t and t[-1] not in ".!?":
        t += "."
    return t


def limit_words(text: str, max_words: int = 40) -> str:
    """Trim to max_words, preferring to stop at the end of a sentence."""
    words = (text or "").split()
    if len(words) <= max_words:
        return (text or "").strip()
    clipped = " ".join(words[:max_words])
    cut = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if cut >= len(clipped) * 0.5:
        return clipped[: cut + 1]
    return clipped.rstrip(",;:- ") + "."


# ---------------------------------------------------------------------------
# VoiceCast: one object per project render that answers "who sounds like what"
# ---------------------------------------------------------------------------
class VoiceCast:
    """
    Holds the cast for one project plus the narrator, and hands out a
    VoiceProfile for any speaker name. Speakers that aren't in the character
    list (an extra the LLM invented mid-dialogue) get a stable voice of their
    own instead of silently reusing someone else's.
    """

    def __init__(self, characters: Sequence, region: str = "auto", narrator_voice: str = "", texts: Iterable[Optional[str]] = ()):
        self.region = detect_region(list(texts), region)
        self.narrator_voice = (narrator_voice or "").strip() or _NARRATOR_VOICE[self.region]
        self.members: List[CastMember] = cast_characters(list(characters), self.region, self.narrator_voice)
        self._extras: Dict[str, CastMember] = {}

    # Persist the decisions back onto DB rows (so they stay stable on re-runs).
    def write_back(self, characters: Sequence) -> bool:
        changed = False
        for ch, m in zip(characters, self.members):
            for attr, val in (("gender", m.gender), ("age_group", m.age_group), ("voice_id", m.voice_id)):
                if not getattr(ch, attr, None):
                    setattr(ch, attr, val)
                    changed = True
        return changed

    def member_for(self, speaker: str) -> CastMember:
        found = match_character(speaker, self.members)
        if found:
            return found
        key = (speaker or "").strip().lower()
        if key not in self._extras:
            ghost = type("Ghost", (), {})()
            ghost.name, ghost.description, ghost.appearance_prompt = speaker or "Voice", "", ""
            ghost.gender = ghost.age_group = None
            ghost.voice_id = None
            taken = [type("T", (), {"voice_id": m.voice_id, "name": m.name, "description": "", "appearance_prompt": "",
                                    "gender": m.gender, "age_group": m.age_group})() for m in self.members + list(self._extras.values())]
            cast = cast_characters(taken + [ghost], self.region, self.narrator_voice)
            self._extras[key] = cast[-1]
        return self._extras[key]

    def profile(self, speaker: str, expression: str = "", action: str = "") -> VoiceProfile:
        if is_narrator(speaker):
            return narrator_profile(self.region, self.narrator_voice, expression)
        return profile_for(self.member_for(speaker), expression, action)

    def signature(self) -> str:
        """Changes whenever anybody's voice assignment changes (used to decide
        whether previously rendered audio can be reused)."""
        return "|".join(f"{m.name}:{m.voice_id}:{m.age_group}" for m in self.members) + f"|N:{self.narrator_voice}"
