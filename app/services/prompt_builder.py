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

_ANIMATED = re.compile(
    r"anim|cartoon|3d|pixar|disney|anime|manga|illustrat|comic|cel[- ]?shad|stylized|stylised|claymation|paint",
    re.IGNORECASE,
)


def clip_words(text: Optional[str], n: int) -> str:
    words = (text or "").replace("\n", " ").split()
    return " ".join(words[:n])


def style_kind(style: Optional[str]) -> str:
    return "animated" if style and _ANIMATED.search(style) else "realistic"


def image_style_suffix(style: Optional[str]) -> str:
    if style_kind(style) == "animated":
        return (
            "stylized 3D animated film still, clean shapes, expressive faces, rich colors, "
            "soft cinematic lighting, sharp details"
        )
    return (
        "cinematic film still, photorealistic, natural skin texture, shallow depth of field, "
        "soft cinematic lighting, sharp focus, 35mm"
    )


def image_negative(style: Optional[str]) -> str:
    base = (
        "blurry, out of focus, low quality, jpeg artifacts, distorted face, asymmetrical face, "
        "deformed hands, extra fingers, extra limbs, bad anatomy, cropped, watermark, text, logo"
    )
    return base + (", photo, photorealistic" if style_kind(style) == "animated" else ", cartoon, illustration, 3d render")


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
