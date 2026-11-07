"""
Builds the text prompts sent to the image model (SDXL) and the video model
(LTX-Video). Pure Python.

Why this exists: both models respond far better to a well-structured prompt
than to the short, terse strings the script-writing LLM produces, and the old
code appended "photorealistic, 8k" to EVERYTHING - even stories whose visual
style was animation.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from app.core.config import settings
from app.services.style_presets import is_cinematic, is_kids
from app.services.voice_service import DEFAULT_ACTIONS
from app.services.delivery import analyze as _analyze_delivery

_ANIMATED = re.compile(
    r"anim|cartoon|3d|pixar|disney|anime|manga|illustrat|comic|cel[- ]?shad|stylized|stylised|claymation|paint|watercolou?r|ghibli",
    re.IGNORECASE,
)


def clip_words(text: Optional[str], n: int) -> str:
    words = (text or "").replace("\n", " ").split()
    return " ".join(words[:n])


def style_kind(style: Optional[str]) -> str:
    """kids | animated | cinematic | realistic"""
    if not style:
        return "realistic"
    if is_kids(style):
        return "kids"
    if _ANIMATED.search(style):
        return "animated"
    if is_cinematic(style):
        return "cinematic"
    return "realistic"


def _is_drawn(kind: str) -> bool:
    return kind in ("kids", "animated")


def image_style_suffix(style: Optional[str]) -> str:
    kind = style_kind(style)
    if kind == "kids":
        return (
            "bright colorful children's animation style, cute friendly characters with big expressive eyes, "
            "soft rounded shapes, warm cheerful lighting, clean simple background, vivid colors, high quality"
        )
    if kind == "animated":
        return (
            "stylized 3D animated film still, clean shapes, expressive faces, rich colors, "
            "soft cinematic lighting, sharp details"
        )
    if kind == "cinematic":
        return (
            "cinematic film still, anamorphic lens, dramatic motivated lighting, shallow depth of field, "
            "rich teal and orange color grading, subtle film grain, high production value, sharp focus"
        )
    return (
        "cinematic film still, photorealistic, natural skin texture, shallow depth of field, "
        "soft cinematic lighting, sharp focus, 35mm"
    )


def image_negative(style: Optional[str]) -> str:
    kind = style_kind(style)
    base = (
        "blurry, out of focus, low quality, jpeg artifacts, distorted face, asymmetrical face, "
        "deformed hands, extra fingers, extra limbs, bad anatomy, cropped, watermark, text, logo"
    )
    if kind == "kids":
        return base + ", scary, dark, creepy, violent, gore, blood, weapon, realistic photo, photorealistic, nsfw"
    if _is_drawn(kind):
        return base + ", photo, photorealistic"
    return base + ", cartoon, illustration, 3d render"


def _style_text(style: Optional[str]) -> str:
    return clip_words(style, 14)


def keyframe_prompt(image_prompt: str, characters: Sequence, style: Optional[str], place_desc: str = "") -> str:
    """The scene's establishing keyframe: the (user-editable) scene prompt,
    plus a short look-description of who is on screen so characters stay
    recognisable from scene to scene."""
    parts = [(image_prompt or "").strip().rstrip(",.")]
    lowered = parts[0].lower()
    looks = []
    for c in list(characters)[:2]:
        appearance = clip_words(getattr(c, "appearance_prompt", ""), 22)
        if appearance and appearance.lower()[:30] not in lowered:
            looks.append(f"{c.name}: {appearance}")
    if looks:
        parts.append("featuring " + "; ".join(looks))
    if place_desc:  # the story bible's saved description of this place: identical in every scene set there
        parts.append("set in " + clip_words(place_desc, 40))
    if style and style.lower()[:20] not in lowered:
        parts.append(_style_text(style))
    parts.append(image_style_suffix(style))
    return ", ".join(p for p in parts if p)


def closeup_prompt(character, expression: str, location: Optional[str], style: Optional[str], free_camera: bool = False) -> str:
    """A dedicated, face-forward keyframe for a speaking character. Big, sharp
    faces are what the video model handles best - tiny faces in a wide shot are
    what 'melt'."""
    name = getattr(character, "name", "the character")
    look = clip_words(getattr(character, "appearance_prompt", "") or getattr(character, "description", ""), 38)
    mood = (expression or "").strip()
    # Free-camera mode frames the speaker waist-up so hands and body can actually act; a tight face is for the static mode.
    framing = "medium shot (waist up)" if free_camera else "medium close-up shot"
    parts = [f"{framing} of {name}, {look}"]
    if mood and mood.lower() not in ("neutral", "dynamic expression"):
        parts.append(f"{mood} expression")
    parts.append("natural relaxed pose, mouth relaxed" if free_camera else "looking slightly off-camera, mouth relaxed")
    if location:
        parts.append(f"in {clip_words(location, 30)}")
    if style:
        parts.append(_style_text(style))
    parts.append(image_style_suffix(style))
    return ", ".join(p for p in parts if p)


_CAMERA_GENTLE = [
    "very slow gentle push-in toward the subject",
    "very slow lateral camera drift",
    "nearly static camera with a very slight drift",
]
_CAMERA_STATIC = "static locked-off camera, tripod shot, the background stays perfectly stable"

_QUALITY_TAIL = "Smooth natural motion, sharp focus, detailed face, consistent character appearance, stable details."


_CAMERA_CINEMATIC = [
    "slow cinematic push-in toward the subject",
    "smooth tracking shot that follows the subject's movement",
    "subtle handheld camera with natural sway",
    "slow arc around the subject with gentle parallax",
    "steady dolly move with a slight rack of focus",
]
_QUALITY_FREE = "Natural, fluid, lifelike motion, cinematic movie look, sharp focus, consistent character appearance."


# ---------------------------------------------------------------------------
# Per-shot camera ("auto") and staged conversations
#
# Why: a rotating set of film moves ("tracking shot that follows the subject", "arc around the subject") told the
# video model that the speaker was moving, so characters paced and walked while talking and never seemed to talk
# TO each other. In "auto" mode a conversation gets a calm camera and characters who stay where they are, facing
# each other (over-the-shoulder), while action beats and establishing shots still get a dynamic camera.
# ---------------------------------------------------------------------------
_ACTION_BEAT = re.compile(
    r"\b(fall(?:s|ing)?|plung\w+|chas\w+|fight\w*|punch\w*|kick\w*|explo\w+|crash\w*|gunfire|shoot\w*|flee\w*|"
    r"escap\w+|leap\w*|jump\w*|dive[sd]?|diving|rac(?:e|es|ed|ing)|skydiv\w+|parachut\w+|fly(?:ing)?|flies|storm\w*|"
    r"collaps\w+|battle\w*|attack\w*|ambush\w*|sprint\w*|swerv\w+|burst\w*|stampede\w*|tumbl\w+)\b",
    re.IGNORECASE,
)
_LOCOMOTION = re.compile(
    r"\b(walk\w*|pac(?:e|es|ed|ing)|stroll\w*|strid(?:e|es|ing)|march\w*|wander\w*|roam\w*|run(?:s|ning)?|jog\w*|"
    r"mov(?:es|ing) around|back and forth|up and down)\b",
    re.IGNORECASE,
)
_CAMERA_DIALOGUE = [
    "steady locked-off camera with a very slight natural drift, the background stays stable",
    "very slow gentle push-in toward the speaker",
    "steady camera with a barely perceptible handheld sway",
]
_CAMERA_ACTION = [
    "fast dynamic tracking shot that follows the action",
    "sweeping dramatic camera move with strong parallax and depth",
    "energetic handheld camera with quick pans and whip-pan reframing",
    "dramatic low-angle camera that rises and pulls back to reveal the whole environment",
]


def is_action_beat(*texts: Optional[str]) -> bool:
    """True when the shot's own action / camera direction describes something high-energy (a fall, chase, fight...)."""
    return any(t and _ACTION_BEAT.search(t) for t in texts)


def strip_locomotion(action: str) -> str:
    """A dialogue action that is about walking / pacing / running becomes empty (the speaker then just gestures)."""
    return "" if (action and _LOCOMOTION.search(action)) else (action or "")


def _is_auto(camera_mode: Optional[str]) -> bool:
    return (camera_mode or "").strip().lower() == "auto"


_SEATED = re.compile(
    r"\b(sit\w*|seated|sat|chair|sofa|couch|bench|stool|desk|table|booth|dinner|lunch|breakfast|bed|bedroom|lying|lies|"
    r"sleep\w*|driv\w+|car|truck|bus|train|cockpit|cab|boat|canoe|elevator|lift|phone call|on the phone|"
    r"hospital bed|wheelchair|cell|prison|interrogat\w+|meeting room|office desk)\b",
    re.IGNORECASE,
)
_NO_BOUNCE = ("smooth, even walking gait, the body stays level, no bouncing, no jumping, no bobbing up and down, "
              "no hopping, no pacing back and forth, no spinning")
_CAMERA_WALK = [
    "smooth gimbal tracking shot that moves alongside them at walking pace, keeping the speaker in frame",
    "steady tracking shot from in front of them, backing away at their walking pace, faces clearly visible",
    "smooth side-on tracking shot that follows them as they walk, with gentle parallax from the background",
]


# What is PHYSICALLY happening decides how people are staged - not a blanket rule. "Oh no, I'm stuck!" must look like
# someone stuck in the mud, struggling in one place, not like someone walking or running around.
_SITUATIONS = [
    ("stuck", re.compile(
        r"\b(stuck|trapped|sink(?:s|ing)?|sunk|quicksand|mire[sd]?|bogged|tangled|entangled|snagged|pinned|wedged|"
        r"stranded|can'?t move|cannot move|can'?t get out|can'?t break free|can'?t pull|held fast|caught (?:in|on|under)|"
        r"(?:knee|waist|neck|chest|ankle)[- ]deep in|up to (?:his|her|their|its) (?:knees|waist|neck|chest))\b", re.I)),
    ("lying", re.compile(
        r"\b(lying|lies|lay|collapsed|unconscious|fainted|passed out|knocked out|injured|wounded|bedridden|"
        r"too (?:weak|hurt) to (?:stand|move|walk))\b", re.I)),
    ("swimming", re.compile(
        r"\b(swim\w*|drown\w*|treading water|afloat|floating in|in the (?:water|river|sea|lake|ocean)|wading)\b", re.I)),
    ("climbing", re.compile(
        r"\b(climb\w*|dangl\w+|hanging (?:from|onto|off)|clinging|cling\w*|scal(?:e|es|ed|ing) (?:the|a) )\b", re.I)),
    ("hiding", re.compile(
        r"\b(hid(?:e|es|ing|den)|crouch\w*|sneak\w*|peek\w*|whisper\w*|(?:in|inside|behind|under) (?:a|the) "
        r"(?:bush|bushes|shrub|hedge|tree|wall|crate|barrel|cupboard|closet|curtain))\b", re.I)),
    ("seated", re.compile(r"\b(sit\w*|seated|sat|chair|sofa|couch|bench|stool|booth|on the bed|in bed)\b", re.I)),
]
_GENERIC_WORDS = re.compile(
    r"\b(?:a|an|the|and|then|while|as|to|at|in|on|of|with|up|down|around|about|slowly|quickly|calmly|gently|nervously|"
    r"talk\w*|speak\w*|say\w*|chat\w*|stand\w*|stay\w*|gestur\w*|nod\w*|look\w*|listen\w*|attentive\w*|react\w*|"
    r"turn\w*|face\w*|smil\w*|neutral|natural\w*|hand\w*|his|her|their|moves?|moving|moves and|"
    r"walk\w*|pac(?:e|es|ed|ing)|stroll\w*|strid(?:e|es|ing)|march\w*|wander\w*|roam\w*|run(?:s|ning)?|jog\w*|"
    r"back|forth)\b", re.I)


def is_generic_action(action: Optional[str]) -> bool:
    """True when the action says nothing specific about what the body is doing (empty, placeholder, just talking /
    standing / walking about). A specific action ('kneels', 'pulls at his legs', 'opens the box') is NOT generic and
    must be performed as written."""
    a = " ".join((action or "").split())
    if not a or a.lower() in DEFAULT_ACTIONS:
        return True
    if _LOCOMOTION.search(a):          # "walks up and down the studio": just moving about, nothing the scene depends on
        return True
    left = _GENERIC_WORDS.sub(" ", a)
    return not re.search(r"[A-Za-z]{3,}", left)


def detect_situation(*texts: Optional[str]) -> str:
    """The physical situation named in these texts: stuck | lying | swimming | climbing | hiding | seated | ''."""
    joined = " . ".join(t for t in texts if t)
    if not joined:
        return ""
    for kind, rx in _SITUATIONS:
        if rx.search(joined):
            return kind
    return ""


def _stuck_subjects(texts: Sequence[Optional[str]], names: Sequence[str]) -> set:
    """Which named characters the 'stuck' sentences are about (so a helper standing beside them is not also stuck)."""
    found = set()
    rx = _SITUATIONS[0][1]
    for t in texts:
        for sent in re.split(r"(?<=[.!?;\n])\s+", t or ""):
            if rx.search(sent):
                for n in names:
                    if n and re.search(r"\b" + re.escape(n) + r"\b", sent, re.I):
                        found.add(n.strip().lower())
    return found


def situation_clause(kind: str, speaker: str, helper_of: str = "") -> str:
    """What the speaker's BODY does in this situation, written positively so the video model shows it."""
    if kind == "stuck" and helper_of:
        return (f"{speaker} stays right beside {helper_of}, bending down and reaching out to help, pulling at them and reacting "
                f"with worry while talking, {speaker} does not walk away")
    if kind == "stuck":
        return (f"{speaker} is stuck fast, legs trapped and sunk in and unable to move from the spot, {speaker} strains, "
                f"twists, heaves and pulls at their legs with both hands, arms reaching out and flailing, struggling to "
                f"get free while talking, {speaker} stays in the same spot and never walks or runs away")
    if kind == "lying":
        return (f"{speaker} lies on the ground, weak and unable to get up, only the head, face and arms move as they talk")
    if kind == "swimming":
        return (f"{speaker} is in the water, treading water and paddling with sweeping arms, head just above the surface, "
                f"struggling to stay afloat while talking")
    if kind == "climbing":
        return (f"{speaker} is climbing, gripping hard with hands and feet, pulling their body upward and holding on tight "
                f"while talking")
    if kind == "hiding":
        return (f"{speaker} stays crouched low and hidden, peeking out carefully and speaking in an urgent hushed voice, "
                f"small cautious movements, staying hidden in the same place")
    if kind == "seated":
        return (f"{speaker} is sitting, leaning toward the other character and talking face to face, gesturing with their "
                f"hands, staying seated")
    return ""


def situation_for(speaker: str, action: str, line: str, context: str, on_screen: Sequence = ()) -> tuple:
    """(kind, helper_of) for one speaker in one shot. The shot's own action / words win over the scene context."""
    own = detect_situation(action, line)
    kind = own or detect_situation(context)
    helper_of = ""
    if kind == "stuck" and not own:
        names = [getattr(c, "name", "") for c in on_screen] + [speaker]
        subjects = _stuck_subjects([context, line], names)
        me = (speaker or "").strip().lower()
        if subjects and me not in subjects:
            helper_of = next((getattr(c, "name", "") for c in on_screen if getattr(c, "name", "").strip().lower() in subjects), "")
            if not helper_of:
                kind = ""
    return kind, helper_of


def staging_mode(*texts: Optional[str]) -> str:
    """'walk' | 'stay' | 'free' for a conversation. 'walk' falls back to 'stay' when the shot / place is clearly
    seated, in a vehicle or in bed (walking there makes no sense) or when it is an action beat."""
    mode = (getattr(settings, "CONVERSATION_STAGING", "walk") or "walk").strip().lower()
    if mode not in ("walk", "stay", "free"):
        mode = "walk"
    if mode == "walk" and any(t and _SEATED.search(t) for t in texts):
        return "stay"
    return mode


def walk_clause(speaker: str, on_screen: Sequence, place: str = "") -> str:
    """The walk-and-talk sentence: both characters keep moving together while they talk."""
    me = (speaker or "").strip().lower()
    others = [c for c in on_screen if getattr(c, "name", "").strip().lower() != me]
    where = f" through {place.split(':')[0].strip()}" if place else ""
    if others:
        names = " and ".join(c.name for c in others[:2])
        return (f"{speaker} and {names} walk side by side{where} at a relaxed, steady pace, heading the same way; "
                f"{speaker} turns toward {names} and talks to them directly while walking, gesturing with one hand, "
                f"{names} glances back, nods and reacts; they keep walking and talking without stopping, "
                f"a real conversation between them; {_NO_BOUNCE}")
    return (f"{speaker} walks steadily forward{where} while talking to the person beside them just off camera, "
            f"eyes and head turning toward them, gesturing naturally; {_NO_BOUNCE}")


def stage_dialogue_bits(speaker: str, look: str, on_screen: Sequence, act: str, mood_txt: str,
                        place: str = "", context: str = "", line: str = "") -> list:
    """The prompt pieces for a speaking character who is TALKING TO someone. What is physically happening decides:
    1) a situation (stuck, lying, swimming, climbing, hiding, seated) is shown with the body; 2) a specific action
    ('kneels', 'opens the box') is performed as written; 3) only when there is nothing specific, the conversation is
    staged by CONVERSATION_STAGING: walk (walk-and-talk) | stay (face each other) | free."""
    kind, helper_of = situation_for(speaker, act, line, context, on_screen)
    if kind:
        framing = "Medium shot" if kind != "stuck" else "Medium shot, low angle"
        bits = [(f"{framing} of {speaker}, {look}" if look else f"{framing} of {speaker}"),
                situation_clause(kind, speaker, helper_of)]
        if act and not is_generic_action(act):
            bits.append(perform(speaker, act))
        bits.append(mood_txt)
        return bits
    if act and not is_generic_action(act):
        bits = [(f"Medium shot of {speaker}, {look}" if look else f"Medium shot of {speaker}"), perform(speaker, act)]
        me = (speaker or "").strip().lower()
        others = [c for c in on_screen if getattr(c, "name", "").strip().lower() != me]
        if others:
            bits.append(f"talking to {others[0].name}, who reacts")
        bits.append(mood_txt)
        return bits
    mode = staging_mode(act, place, context)
    if mode == "walk":
        bits = [(f"Medium tracking shot of {speaker}, {look}" if look else f"Medium tracking shot of {speaker}"),
                walk_clause(speaker, on_screen, place), mood_txt]
        return bits
    if mode == "free":
        return [(f"Medium shot of {speaker}, {look}" if look else f"Medium shot of {speaker}"),
                f"{speaker} talks to the other character, moving and gesturing naturally", mood_txt]
    return _stage_still_bits(speaker, look, on_screen, act, mood_txt)


def _stage_still_bits(speaker: str, look: str, on_screen: Sequence, act: str, mood_txt: str) -> list:
    """The prompt pieces for a speaking character who is TALKING TO someone: both stay put and face each other."""
    me = (speaker or "").strip().lower()
    others = [c for c in on_screen if getattr(c, "name", "").strip().lower() != me]
    calm = strip_locomotion(act)
    if others:
        other = others[0]
        o_look = clip_words(getattr(other, "appearance_prompt", "") or getattr(other, "description", ""), 18)
        bits = [
            (f"Over-the-shoulder medium shot of {speaker}, {look}" if look else f"Over-the-shoulder medium shot of {speaker}"),
            f"{speaker} stands in one place facing {other.name} and talks to them directly, a real face-to-face conversation",
            f"{other.name}{f' ({o_look})' if o_look else ''} is in the foreground, shoulder and side of the head visible, "
            "listening and reacting",
        ]
    else:
        bits = [
            (f"Medium close-up of {speaker}, {look}" if look else f"Medium close-up of {speaker}"),
            f"{speaker} stays in one place and speaks naturally, eyes on the person they are talking to just off camera",
        ]
    bits.append(perform(speaker, calm) if calm else "natural hand gestures and an expressive face")
    bits.append(mood_txt)
    bits.append("no walking, no pacing, the characters keep their positions")
    return bits


def is_free_camera(camera_mode: Optional[str]) -> bool:
    return (camera_mode or "model").strip().lower() not in ("static", "gentle")


_CAMERA_STRUGGLE = [
    "steady medium shot with a slight handheld shake that stays on the struggling character",
    "slow push-in on the character straining to get free, then a gentle handheld drift",
]


def camera_clause(camera_mode: Optional[str], shot_index: int, scene_motion: Optional[str] = None,
                  kind: str = "dialogue", action: str = "", context: str = "") -> str:
    """The camera sentence for one shot. 'static' / 'gentle' keep the old locked-off behaviour; anything else
    (default 'model') lets the camera move like in a normal film: the scene's own camera direction on its first
    shot, then a rotating set of classic moves so consecutive shots are not all the same."""
    mode = (camera_mode or "model").strip().lower()
    if mode == "auto":
        # Chosen shot by shot: action beats get a dynamic camera, conversations a calm one, everything else a film move.
        motion_txt = clip_words(scene_motion, 26)
        if is_action_beat(action, scene_motion):
            base = _CAMERA_ACTION[shot_index % len(_CAMERA_ACTION)]
            return f"Camera: {motion_txt}; {base}" if motion_txt else f"Camera: {base}"
        if kind == "dialogue":
            sit = detect_situation(action, context)
            if sit == "stuck":
                return "Camera: " + _CAMERA_STRUGGLE[shot_index % len(_CAMERA_STRUGGLE)]
            if sit:
                return "Camera: " + _CAMERA_DIALOGUE[shot_index % len(_CAMERA_DIALOGUE)]
            if (action and not is_generic_action(action)):
                return "Camera: " + _CAMERA_DIALOGUE[shot_index % len(_CAMERA_DIALOGUE)]
            if staging_mode(action, scene_motion, context) == "walk":
                return "Camera: " + _CAMERA_WALK[shot_index % len(_CAMERA_WALK)]
            return "Camera: " + _CAMERA_DIALOGUE[shot_index % len(_CAMERA_DIALOGUE)]
        if motion_txt and shot_index == 0:
            return f"Camera: {motion_txt}"
        return "Camera: " + _CAMERA_CINEMATIC[(shot_index + (1 if motion_txt else 0)) % len(_CAMERA_CINEMATIC)]
    if mode == "static":
        return _CAMERA_STATIC
    if mode == "gentle":
        return _CAMERA_GENTLE[shot_index % len(_CAMERA_GENTLE)]
    motion = clip_words(scene_motion, 26)
    if motion and shot_index == 0:
        return f"Camera: {motion}"
    return "Camera: " + _CAMERA_CINEMATIC[(shot_index + (1 if motion else 0)) % len(_CAMERA_CINEMATIC)]


def perform(speaker: str, action: str) -> str:
    """'Kobi is tapping on the mixing board' from an action string such as 'Irritated Tapping on the mixing board'
    or 'turns away'. Gerund openers get 'is'; a leading mood word ('Irritated ...') becomes 'is irritated, ...'."""
    act = " ".join((action or "").split())
    if not act or act.lower() in DEFAULT_ACTIONS:   # placeholder actions like "stands attentively" would freeze the character
        return f"{speaker} moves and gestures naturally"
    words = [w.lower() if w[:1].isupper() and w.lower().endswith(("ing", "ed", "ly")) else w for w in act.split()]
    words[0] = words[0][:1].lower() + words[0][1:]
    first = words[0].lower().rstrip(",")
    if len(words) > 1 and first.endswith(("ed", "ly")) and words[1].lower().endswith("ing"):
        return f"{speaker} is {first}, {' '.join(words[1:])}"
    if first.endswith("ing"):
        return f"{speaker} is {' '.join(words)}"
    return f"{speaker} {' '.join(words)}"


def speech_clause(speaker: str, speech: str, voice_desc: str = "", mood: str = "", off_screen: bool = False,
                  action: str = "", scene: str = "") -> str:
    """What the video model should SAY in this clip. LTX-2.x speaks text that is written in quotes together with who
    says it and how - the action is written separately, so it is performed instead of being read out.
    The HOW (loud, whispered, harsh, trembling...) comes from the emotion, the scene and the line itself."""
    who = "An off-screen narrator" if off_screen else (speaker or "The character")
    sit = detect_situation(action, scene) if not off_screen else ""
    d = _analyze_delivery("" if off_screen else mood, action, speech, sit, scene)
    manner = d.phrase()
    voice = f" {voice_desc}" if voice_desc else ""
    if voice and manner:
        voice = f" in {voice_desc.removeprefix('in ').strip()}, {manner.removeprefix('in ')}"
    elif voice:
        voice = f" in {voice_desc.removeprefix('in ').strip()}"
    elif manner:
        voice = f" {manner}"
    emo = (mood or "").strip()
    feel = f", {emo}," if emo and emo.lower() not in ("neutral", "dynamic expression") and not off_screen else ""
    return f'{who} says{voice}{feel}: "{speech}"'


def _audio_clause(sound_design: Optional[str], speech: str, model_voice: bool) -> str:
    cue = clip_words(sound_design, 22) or "natural ambient sound of the location"
    if model_voice and speech:
        return (f"Audio: the spoken line is clearly audible and lip-synced, said in full and unhurried, every word to the end "
                f"of the sentence before the shot ends, with {cue} quietly underneath, no background music")
    return f"Audio: {cue}, no spoken dialogue, no speech, no background music"


def video_prompt(
    kind: str,
    speaker: str,
    expression: str,
    action: str,
    scene_motion: Optional[str],
    visual_description: Optional[str],
    shot_index: int,
    camera_mode: str = "model",
    speech: str = "",
    voice_desc: str = "",
    sound_design: Optional[str] = None,
    model_voice: bool = False,
    dialogue_text: str = "",
) -> str:
    """
    One flowing paragraph describing the MOTION (and, in model-voice mode, the spoken line) for LTX.

    camera_mode="model" (default): free cinematic camera. "static"/"gentle" keep the old locked-off look.
    `action` is performed by the character; `speech` (model-voice mode) is what they say - the two are kept apart.
    """
    free = is_free_camera(camera_mode)
    camera = camera_clause(camera_mode, shot_index, scene_motion, kind, action, f"{visual_description or ''} {dialogue_text}")
    auto_calm = _is_auto(camera_mode) and kind == "dialogue" and not is_action_beat(action, scene_motion)
    motion = "" if free else ("" if (camera_mode or "").lower() == "static" else clip_words(scene_motion, 22))
    setting = clip_words(visual_description, 24)
    act = clip_words(action, 18)
    mood = (expression or "").strip()
    mood_txt = "" if mood.lower() in ("neutral", "dynamic expression", "") else f"{mood} expression"

    if kind == "dialogue":
        if auto_calm:
            sit, helper = situation_for(speaker, act, dialogue_text or speech, f"{visual_description or ''} {scene_motion or ''}")
            if sit:
                bits = [situation_clause(sit, speaker, helper), mood_txt]
            elif act and not is_generic_action(act):
                bits = [perform(speaker, act), mood_txt]
            elif staging_mode(act, scene_motion, visual_description) == "walk":
                bits = [f"{speaker} walks steadily forward while talking to the other character beside them, turning toward "
                        f"them and gesturing naturally, they keep walking and talking together; {_NO_BOUNCE}", mood_txt]
            else:
                calm = strip_locomotion(act)
                bits = [perform(speaker, calm) if calm else f"{speaker} stays in place and gestures naturally while talking to the other character",
                        mood_txt, "no walking, no pacing, the characters keep their positions"]
            if model_voice and speech:
                bits.append(speech_clause(speaker, speech, voice_desc, mood, action=act, scene=f"{visual_description or ''} {scene_motion or ''}"))
            else:
                bits.append("speaking, mouth moving naturally as they talk")
            bits += [f"setting: {setting}" if setting else "", camera]
        elif free:
            sit, helper = situation_for(speaker, act, dialogue_text or speech, f"{visual_description or ''} {scene_motion or ''}")
            bits = [situation_clause(sit, speaker, helper) if (sit and is_generic_action(act)) else perform(speaker, act), mood_txt]
            if model_voice and speech:
                bits.append(speech_clause(speaker, speech, voice_desc, mood, action=act, scene=f"{visual_description or ''} {scene_motion or ''}"))
            else:
                bits.append("speaking, mouth moving naturally as they talk")
            bits += [f"setting: {setting}" if setting else "", camera]
        else:
            bits = [
                f"Close shot of {speaker} speaking, mouth moving naturally as they talk, {mood_txt}, {act}".replace(", ,", ",").rstrip(", "),
                "subtle head and shoulder movement, natural blinking, only small gentle motion",
                f"setting: {setting}" if setting else "",
                camera, motion,
            ]
            if model_voice and speech:
                bits.append(speech_clause(speaker, speech, voice_desc, mood, action=act, scene=f"{visual_description or ''} {scene_motion or ''}"))
    else:
        bits = [
            setting or "the scene",
            "people and the environment move naturally, light, leaves and fabric stir" if free
            else "gentle natural movement in the environment such as light, leaves or fabric, people move slowly",
            camera, motion,
        ]
        if model_voice and speech:
            bits.append(speech_clause(speaker, speech, voice_desc, mood, off_screen=True))
    if model_voice:
        bits.append(_audio_clause(sound_design, speech, True))
    paragraph = ". ".join(b.strip().rstrip(".") for b in bits if b and b.strip()).replace('". ', '" ')
    return f"{paragraph}. " + (_QUALITY_FREE if free else _QUALITY_TAIL)


# ---------------------------------------------------------------------------
# Text-to-video prompts (LTX-2 style): ONE flowing, present-tense paragraph that
# carries everything the model needs - there is no start image, so the
# character's look, the place, the light and the camera must all be in the text.
# Repeating each character's exact appearance in every shot is what keeps them
# recognisable from clip to clip.
# ---------------------------------------------------------------------------
def _t2v_style(style: Optional[str]) -> str:
    kind = style_kind(style)
    if kind == "kids":
        return ("bright cheerful children's cartoon animation, cute rounded characters with big expressive eyes, "
                "vivid saturated colors, soft warm light, smooth gentle motion, friendly and safe")
    if kind == "animated":
        return "stylized 3D animated film look, clean shapes, expressive faces, rich colors, soft cinematic lighting"
    if kind == "cinematic":
        return ("cinematic film look, anamorphic lens, dramatic motivated lighting, shallow depth of field, "
                "rich color grading, subtle film grain, high production value")
    return "cinematic live-action film look, natural lighting, shallow depth of field, realistic skin texture, 35mm"


def text_video_prompt(
    kind: str,
    speaker: str,
    expression: str,
    action: str,
    speaker_look: Optional[str],
    on_screen: Sequence,
    location: Optional[str],
    visual_description: Optional[str],
    style: Optional[str],
    shot_index: int,
    camera_mode: str = "model",
    sound_design: Optional[str] = None,
    audio_hybrid: bool = False,
    scene_motion: Optional[str] = None,
    speech: str = "",
    voice_desc: str = "",
    model_voice: bool = False,
    dialogue_text: str = "",
) -> str:
    free = is_free_camera(camera_mode)
    camera = camera_clause(camera_mode, shot_index, scene_motion, kind, action, f"{(location or '').split(':')[0]} {visual_description or ''} {dialogue_text}")
    staged = _is_auto(camera_mode) and kind == "dialogue" and not is_action_beat(action, scene_motion)
    place = clip_words(location, 30)
    setting = clip_words(visual_description, 26)
    act = clip_words(action, 18)
    mood = (expression or "").strip()
    mood_txt = "" if mood.lower() in ("neutral", "dynamic expression", "") else f"with {'an' if mood.strip()[:1].lower() in 'aeiou' else 'a'} {mood} expression"

    if kind == "dialogue":
        look = clip_words(speaker_look, 40)
        framing = "Medium shot" if free else "Medium close-up"
        bits = [f"{framing} of {speaker}, {look}".rstrip(", ") if look else f"{framing} of {speaker}"]
        if staged:
            bits = stage_dialogue_bits(speaker, look, on_screen, act, mood_txt, place.split(':')[0].strip(), f"{(location or '').split(':')[0]} {visual_description or ''} {scene_motion or ''}", dialogue_text or speech)
        elif free:
            sit, helper = situation_for(speaker, act, dialogue_text or speech, f"{(location or '').split(':')[0]} {visual_description or ''} {scene_motion or ''}", on_screen)
            bits.append(situation_clause(sit, speaker, helper) if (sit and is_generic_action(act)) else perform(speaker, act))
            bits.append(mood_txt)
        else:
            bits.append(f"{speaker} is speaking, mouth moving naturally as they talk, {mood_txt}, {act}".replace(", ,", ",").rstrip(", "))
            bits.append("subtle head and shoulder movement, natural blinking, only small gentle motion")
        if model_voice and speech:
            bits.append(speech_clause(speaker, speech, voice_desc, mood, action=act, scene=f"{visual_description or ''} {scene_motion or ''}"))
        elif free:
            bits.append("speaking, mouth moving naturally as they talk")
        bits += [f"in {place}" if place else "", setting]
    else:
        looks = []
        for c in list(on_screen)[:2]:
            a = clip_words(getattr(c, "appearance_prompt", ""), 24)
            if a:
                looks.append(f"{c.name}, {a}")
        bits = [
            "Wide establishing shot" + (f" in {place}" if place else ""),
            setting,
            ("showing " + "; ".join(looks)) if looks else "",
            "people and the environment move naturally, light, leaves and fabric stir" if free
            else "gentle natural movement in the environment such as light, leaves or fabric, people move slowly",
        ]
        if model_voice and speech:
            bits.append(speech_clause(speaker, speech, voice_desc, mood, off_screen=True))
    bits += [camera, _t2v_style(style),
             "Natural, fluid, lifelike motion, sharp focus, consistent character appearance" if free
             else "Smooth natural motion, sharp focus, stable details, consistent character appearance"]
    if model_voice:
        bits.append(_audio_clause(sound_design, speech, True))
    elif audio_hybrid:
        # The characters' voices are recorded separately by the pipeline, so ask the video model for
        # ONLY the background sound: no speech of its own (it would clash), no music.
        bits.append(_audio_clause(sound_design, "", False))
    return ". ".join(b.strip().rstrip(".") for b in bits if b and b.strip()).replace('". ', '" ') + "."



def whole_scene_prompt(
    beats: Sequence[dict],
    on_screen: Sequence,
    location: Optional[str],
    visual_description: Optional[str],
    style: Optional[str],
    scene_motion: Optional[str] = None,
    sound_design: Optional[str] = None,
    start_image: bool = False,
) -> str:
    """ONE prompt for a whole scene (or a long part of one). Beats are in order; each is a dict with
    kind ("dialogue"|"narration"), speaker, expression, action, speech, voice_desc.
    LTX-2.x follows a flowing, chronological description: the place and cast first, then what happens in order,
    with each spoken line in quotes next to who says it. Action is written as action, speech only in quotes."""
    place = clip_words(location, 30)
    setting = clip_words(visual_description, 40)
    motion = clip_words(scene_motion, 26)
    cast_bits = []
    for c in list(on_screen)[:4]:
        look = clip_words(getattr(c, "appearance_prompt", "") or getattr(c, "description", ""), 30)
        cast_bits.append(f"{c.name}, {look}" if look else c.name)
    lead = "Continuing the same shot" if start_image else (f"A film scene in {place}" if place else "A film scene")
    steps = []
    order = ["First", "Then", "Next", "After that", "Finally"]
    spoken = 0
    scene_ctx = f"{place.split(':')[0]} {setting} {motion}"
    for i, b in enumerate(beats):
        word = order[min(i, len(order) - 1)] if len(beats) > 1 else ""
        speech = b.get("speech") or ""
        if b.get("kind") == "narration":
            line = speech_clause(b.get("speaker") or "Narrator", speech, b.get("voice_desc", ""), "", off_screen=True) if speech else ""
        else:
            who = b.get("speaker") or "A character"
            raw_act = clip_words(b.get("action"), 18)
            sit, helper = situation_for(who, raw_act, speech, scene_ctx, on_screen)
            # A scene's situation (stuck in mud, hiding, swimming...) is shown by the body in EVERY beat of it.
            line = situation_clause(sit, who, helper) if (sit and is_generic_action(raw_act)) else perform(who, raw_act)
            if sit and not is_generic_action(raw_act):
                line = perform(who, raw_act)
            if speech:
                line += ", and " + speech_clause(who, speech, b.get("voice_desc", ""), b.get("expression", ""), action=raw_act, scene=scene_ctx)
        if speech:
            spoken += 1
        if line:
            steps.append(f"{word + ': ' if word else ''}{line}".strip())
    bits = [lead, setting, ("Characters: " + "; ".join(cast_bits)) if cast_bits else ""]
    talkers = {b.get("speaker") for b in beats if b.get("kind") != "narration" and b.get("speech")}
    scene_sit = detect_situation(scene_ctx, " ".join(f"{b.get('action') or ''} {b.get('speech') or ''}" for b in beats))
    walking = (len(cast_bits) >= 2 and len(talkers) >= 1 and not scene_sit
               and not any(is_action_beat(b.get("action")) for b in beats)
               and all(is_generic_action(b.get("action")) for b in beats if b.get("kind") != "narration")
               and staging_mode(" ".join(str(b.get("action") or "") for b in beats), place, setting, motion) == "walk")
    if walking:
        bits.append("While they talk the characters walk together through the place at an easy, steady pace, turning to "
                    "each other, gesturing and reacting, a real flowing conversation without stopping; " + _NO_BOUNCE)
    if scene_sit == "stuck":
        bits.append("The character who is stuck stays trapped in the same spot for the whole scene and never walks or runs; "
                    "the others stay close and react")
    bits += steps
    if walking:
        camera = ("Camera: smooth tracking shots that move with the walking characters, alternating between a two-shot "
                  "and a medium shot of whoever is speaking" + (f", {motion}" if motion else ""))
    elif scene_sit == "stuck":
        camera = ("Camera: steady medium shots with a slight handheld shake that stay on the struggling character, "
                  "with close-ups of the effort and of the others reacting" + (f", {motion}" if motion else ""))
    else:
        camera = (f"Camera: {motion}, and it keeps moving like a real film, changing angle and framing to follow whoever is "
                  "speaking, with natural cuts between wide, medium and close-up shots") if motion else (
                  "Camera: moves like a real film, changing angle and framing to follow whoever is speaking, with natural "
                  "cuts between wide, medium and close-up shots")
    bits += [camera, _t2v_style(style),
             "Natural, fluid, lifelike motion, sharp focus, consistent character appearance throughout"]
    if spoken:
        bits.append("The scene runs until the last spoken line is completely finished, then holds for a moment")
    cue = clip_words(sound_design, 22) or "natural ambient sound of the location"
    bits.append(f"Audio: every spoken line is clearly audible, in the order given, and lip-synced, with {cue} quietly "
                "underneath, no background music" if spoken else f"Audio: {cue}, no spoken dialogue, no background music")
    return ". ".join(b.strip().rstrip(".") for b in bits if b and b.strip()).replace('". ', '" ') + "."
