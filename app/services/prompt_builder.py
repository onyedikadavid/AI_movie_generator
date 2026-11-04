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

from app.services.style_presets import is_cinematic, is_kids

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


def keyframe_prompt(image_prompt: str, characters: Sequence, style: Optional[str]) -> str:
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
        parts.append(f"in {clip_words(location, 12)}")
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


def stage_dialogue_bits(speaker: str, look: str, on_screen: Sequence, act: str, mood_txt: str) -> list:
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


def camera_clause(camera_mode: Optional[str], shot_index: int, scene_motion: Optional[str] = None,
                  kind: str = "dialogue", action: str = "") -> str:
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
    if not act:
        return f"{speaker} moves and gestures naturally"
    words = [w.lower() if w[:1].isupper() and w.lower().endswith(("ing", "ed", "ly")) else w for w in act.split()]
    words[0] = words[0][:1].lower() + words[0][1:]
    first = words[0].lower().rstrip(",")
    if len(words) > 1 and first.endswith(("ed", "ly")) and words[1].lower().endswith("ing"):
        return f"{speaker} is {first}, {' '.join(words[1:])}"
    if first.endswith("ing"):
        return f"{speaker} is {' '.join(words)}"
    return f"{speaker} {' '.join(words)}"


def speech_clause(speaker: str, speech: str, voice_desc: str = "", mood: str = "", off_screen: bool = False) -> str:
    """What the video model should SAY in this clip. LTX-2.x speaks text that is written in quotes together with who
    says it and how - the action is written separately, so it is performed instead of being read out."""
    who = "An off-screen narrator" if off_screen else (speaker or "The character")
    voice = f" in {voice_desc}" if voice_desc else ""
    mood = (mood or "").strip()
    manner = f", {mood}," if mood and mood.lower() not in ("neutral", "dynamic expression") else ""
    return f'{who} says{voice}{manner}: "{speech}"'


def _audio_clause(sound_design: Optional[str], speech: str, model_voice: bool) -> str:
    cue = clip_words(sound_design, 22) or "natural ambient sound of the location"
    if model_voice and speech:
        return f"Audio: the spoken line is clearly audible and lip-synced, with {cue} quietly underneath, no background music"
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
) -> str:
    """
    One flowing paragraph describing the MOTION (and, in model-voice mode, the spoken line) for LTX.

    camera_mode="model" (default): free cinematic camera. "static"/"gentle" keep the old locked-off look.
    `action` is performed by the character; `speech` (model-voice mode) is what they say - the two are kept apart.
    """
    free = is_free_camera(camera_mode)
    camera = camera_clause(camera_mode, shot_index, scene_motion, kind, action)
    auto_calm = _is_auto(camera_mode) and kind == "dialogue" and not is_action_beat(action, scene_motion)
    motion = "" if free else ("" if (camera_mode or "").lower() == "static" else clip_words(scene_motion, 22))
    setting = clip_words(visual_description, 24)
    act = clip_words(action, 18)
    mood = (expression or "").strip()
    mood_txt = "" if mood.lower() in ("neutral", "dynamic expression", "") else f"{mood} expression"

    if kind == "dialogue":
        if auto_calm:
            calm = strip_locomotion(act)
            bits = [perform(speaker, calm) if calm else f"{speaker} stays in place and gestures naturally while talking to the other character",
                    mood_txt, "no walking, no pacing, the characters keep their positions"]
            if model_voice and speech:
                bits.append(speech_clause(speaker, speech, voice_desc, mood))
            else:
                bits.append("speaking, mouth moving naturally as they talk")
            bits += [f"setting: {setting}" if setting else "", camera]
        elif free:
            bits = [perform(speaker, act), mood_txt]
            if model_voice and speech:
                bits.append(speech_clause(speaker, speech, voice_desc, mood))
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
                bits.append(speech_clause(speaker, speech, voice_desc, mood))
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
) -> str:
    free = is_free_camera(camera_mode)
    camera = camera_clause(camera_mode, shot_index, scene_motion, kind, action)
    staged = _is_auto(camera_mode) and kind == "dialogue" and not is_action_beat(action, scene_motion)
    place = clip_words(location, 10)
    setting = clip_words(visual_description, 26)
    act = clip_words(action, 18)
    mood = (expression or "").strip()
    mood_txt = "" if mood.lower() in ("neutral", "dynamic expression", "") else f"with {'an' if mood.strip()[:1].lower() in 'aeiou' else 'a'} {mood} expression"

    if kind == "dialogue":
        look = clip_words(speaker_look, 40)
        framing = "Medium shot" if free else "Medium close-up"
        bits = [f"{framing} of {speaker}, {look}".rstrip(", ") if look else f"{framing} of {speaker}"]
        if staged:
            bits = stage_dialogue_bits(speaker, look, on_screen, act, mood_txt)
        elif free:
            bits.append(perform(speaker, act))
            bits.append(mood_txt)
        else:
            bits.append(f"{speaker} is speaking, mouth moving naturally as they talk, {mood_txt}, {act}".replace(", ,", ",").rstrip(", "))
            bits.append("subtle head and shoulder movement, natural blinking, only small gentle motion")
        if model_voice and speech:
            bits.append(speech_clause(speaker, speech, voice_desc, mood))
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
