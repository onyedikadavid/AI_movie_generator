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


def closeup_prompt(character, expression: str, location: Optional[str], style: Optional[str]) -> str:
    """A dedicated, face-forward keyframe for a speaking character. Big, sharp
    faces are what the video model handles best - tiny faces in a wide shot are
    what 'melt'."""
    name = getattr(character, "name", "the character")
    look = clip_words(getattr(character, "appearance_prompt", "") or getattr(character, "description", ""), 38)
    mood = (expression or "").strip()
    parts = [f"medium close-up shot of {name}, {look}"]
    if mood and mood.lower() not in ("neutral", "dynamic expression"):
        parts.append(f"{mood} expression")
    parts.append("looking slightly off-camera, mouth relaxed")
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


def video_prompt(
    kind: str,
    speaker: str,
    expression: str,
    action: str,
    scene_motion: Optional[str],
    visual_description: Optional[str],
    shot_index: int,
    camera_mode: str = "static",
) -> str:
    """
    One flowing paragraph describing the MOTION for LTX-Video.

    camera_mode="static" (default): the camera never moves - only the subject
    does. Camera moves (push-ins, drifts, handheld sway) are what make this
    class of video model bend and smear the whole frame, so they are off unless
    you ask for "gentle".
    """
    static = (camera_mode or "static").lower() != "gentle"
    camera = _CAMERA_STATIC if static else _CAMERA_GENTLE[shot_index % len(_CAMERA_GENTLE)]
    # The scene's motion prompt usually describes camera work; ignore it in static mode.
    motion = "" if static else clip_words(scene_motion, 22)
    setting = clip_words(visual_description, 24)

    if kind == "dialogue":
        mood = (expression or "").strip()
        mood = "" if mood.lower() in ("neutral", "dynamic expression") else f"{mood} expression, "
        act = clip_words(action, 14)
        bits = [
            f"Close shot of {speaker} speaking, mouth moving naturally as they talk, {mood}{act}".rstrip(", "),
            "subtle head and shoulder movement, natural blinking, only small gentle motion",
            f"setting: {setting}" if setting else "",
            camera,
            motion,
        ]
    else:
        bits = [
            setting or "the scene",
            "gentle natural movement in the environment such as light, leaves or fabric, people move slowly",
            camera,
            motion,
        ]
    paragraph = ". ".join(b.strip().rstrip(".") for b in bits if b and b.strip())
    return f"{paragraph}. {_QUALITY_TAIL}"


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
    camera_mode: str = "static",
    sound_design: Optional[str] = None,
    audio_hybrid: bool = False,
) -> str:
    static = (camera_mode or "static").lower() != "gentle"
    camera = _CAMERA_STATIC if static else _CAMERA_GENTLE[shot_index % len(_CAMERA_GENTLE)]
    place = clip_words(location, 10)
    setting = clip_words(visual_description, 26)

    if kind == "dialogue":
        mood = (expression or "").strip()
        mood = "" if mood.lower() in ("neutral", "dynamic expression") else f"with a {mood} expression, "
        act = clip_words(action, 12)
        look = clip_words(speaker_look, 40)
        bits = [
            f"Medium close-up of {speaker}, {look}".rstrip(", ") if look else f"Medium close-up of {speaker}",
            f"{speaker} is speaking, mouth moving naturally as they talk, {mood}{act}".rstrip(", "),
            "subtle head and shoulder movement, natural blinking, only small gentle motion",
            f"in {place}" if place else "",
            setting,
        ]
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
            "gentle natural movement in the environment such as light, leaves or fabric, people move slowly",
        ]
    bits += [camera, _t2v_style(style), "Smooth natural motion, sharp focus, stable details, consistent character appearance"]
    if audio_hybrid:
        # The characters' voices are recorded separately by the pipeline, so ask the video model for
        # ONLY the background sound: no speech of its own (it would clash), no music.
        cue = clip_words(sound_design, 22) or "natural ambient sound of the location"
        bits.append(f"Audio: {cue}, no spoken dialogue, no speech, no background music")
    return ". ".join(b.strip().rstrip(".") for b in bits if b and b.strip()) + "."
