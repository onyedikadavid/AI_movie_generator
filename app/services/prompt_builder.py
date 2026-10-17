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
from app.services.voice_service import DEFAULT_ACTIONS

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


def is_free_camera(camera_mode: Optional[str]) -> bool:
    return (camera_mode or "model").strip().lower() not in ("static", "gentle")


def camera_clause(camera_mode: Optional[str], shot_index: int, scene_motion: Optional[str] = None) -> str:
    """The camera sentence for one shot. 'static' / 'gentle' keep the old locked-off behaviour; anything else
    (default 'model') lets the camera move like in a normal film: the scene's own camera direction on its first
    shot, then a rotating set of classic moves so consecutive shots are not all the same."""
    mode = (camera_mode or "model").strip().lower()
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
    camera = camera_clause(camera_mode, shot_index, scene_motion)
    motion = "" if free else ("" if (camera_mode or "").lower() == "static" else clip_words(scene_motion, 22))
    setting = clip_words(visual_description, 24)
    act = clip_words(action, 18)
    mood = (expression or "").strip()
    mood_txt = "" if mood.lower() in ("neutral", "dynamic expression", "") else f"{mood} expression"

    if kind == "dialogue":
        if free:
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
    camera = camera_clause(camera_mode, shot_index, scene_motion)
    place = clip_words(location, 10)
    setting = clip_words(visual_description, 26)
    act = clip_words(action, 18)
    mood = (expression or "").strip()
    mood_txt = "" if mood.lower() in ("neutral", "dynamic expression", "") else f"with a {mood} expression"

    if kind == "dialogue":
        look = clip_words(speaker_look, 40)
        framing = "Medium shot" if free else "Medium close-up"
        bits = [f"{framing} of {speaker}, {look}".rstrip(", ") if look else f"{framing} of {speaker}"]
        if free:
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
    place = clip_words(location, 10)
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
    for i, b in enumerate(beats):
        word = order[min(i, len(order) - 1)] if len(beats) > 1 else ""
        speech = b.get("speech") or ""
        if b.get("kind") == "narration":
            line = speech_clause(b.get("speaker") or "Narrator", speech, b.get("voice_desc", ""), "", off_screen=True) if speech else ""
        else:
            who = b.get("speaker") or "A character"
            act = perform(who, clip_words(b.get("action"), 18))
            line = act
            if speech:
                line += ", and " + speech_clause(who, speech, b.get("voice_desc", ""), b.get("expression", ""))
        if speech:
            spoken += 1
        if line:
            steps.append(f"{word + ': ' if word else ''}{line}".strip())
    bits = [lead, setting, ("Characters: " + "; ".join(cast_bits)) if cast_bits else ""]
    bits += steps
    camera = (f"Camera: {motion}, and it keeps moving like a real film, changing angle and framing to follow whoever is "
              "speaking, with natural cuts between wide, medium and close-up shots") if motion else (
              "Camera: moves like a real film, changing angle and framing to follow whoever is speaking, with natural "
              "cuts between wide, medium and close-up shots")
    bits += [camera, _t2v_style(style),
             "Natural, fluid, lifelike motion, sharp focus, consistent character appearance throughout"]
    cue = clip_words(sound_design, 22) or "natural ambient sound of the location"
    bits.append(f"Audio: every spoken line is clearly audible, in the order given, and lip-synced, with {cue} quietly "
                "underneath, no background music" if spoken else f"Audio: {cue}, no spoken dialogue, no background music")
    return ". ".join(b.strip().rstrip(".") for b in bits if b and b.strip()).replace('". ', '" ') + "."
