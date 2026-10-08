"""
Style presets chosen on the "New Project" form: what "Kids" and "Cinematic" mean.

Pure Python (no I/O) so it can be shared by the script writer (llm_service),
the dialogue agents and the image/video prompt builder.
"""
from __future__ import annotations

import re
from typing import Optional

_KIDS = re.compile(
    r"\b(kids?|child(?:ren)?(?:'s)?|toddlers?|preschool\w*|nursery|family[- ]friendly|bedtime)\b", re.IGNORECASE
)
_CINEMATIC = re.compile(r"cinematic|anamorphic|film[- ]?look|movie[- ]?look", re.IGNORECASE)

KIDS_STYLE_LABEL = "Kids cartoon (child-friendly)"
CINEMATIC_STYLE_LABEL = "Cinematic"


def is_kids(*texts: Optional[str]) -> bool:
    return any(t and _KIDS.search(t) for t in texts)


def is_cinematic(*texts: Optional[str]) -> bool:
    return any(t and _CINEMATIC.search(t) for t in texts)


def resolve_style(
    requested_style: Optional[str],
    llm_style: Optional[str],
    requested_genre: Optional[str] = None,
    requested_tone: Optional[str] = None,
) -> str:
    """The visual style used for every image/video prompt.
    The user's own choice wins over the script writer's paraphrase of it, and picking the
    Kids genre/tone always yields a kids look even if no visual style was chosen."""
    style = (requested_style or "").strip() or (llm_style or "").strip()
    if is_kids(requested_genre, requested_tone, requested_style) and not is_kids(style):
        style = f"{KIDS_STYLE_LABEL}, {style}".strip(", ")
    return style


# Instructions added to the script-writing prompt.
KIDS_RULES = """
        - AUDIENCE: young children (about 3 to 8 years old). This MUST be a child-friendly story:
            * absolutely no violence, weapons, blood, injury, death, cruelty, scary or frightening scenes,
              romance, drugs, alcohol, or any adult theme - if the idea contains any, tell it gently and
              kindly instead (conflict is a misunderstanding that is solved with kindness)
            * simple, short sentences and easy everyday words in the narration and in every spoken line
            * a warm, cheerful, playful voice; friendly, curious, cute characters; a clear happy ending
              with a small gentle lesson
            * bright, safe, colourful settings
            * character appearance: round, friendly, expressive faces with big eyes; bright clothes
        - visual_style must be a bright children's cartoon animation look"""

CINEMATIC_RULES = """
        - Write image_prompt and motion_prompt like a cinematographer: shot size (wide / medium / close-up),
          lens feel, lighting direction and colour mood (e.g. 'low golden-hour backlight, teal and orange grade'),
          depth of field, and one clear focal subject per shot
        - Favour dramatic, motivated lighting, strong composition and a filmic colour palette"""

KIDS_DIALOGUE_RULE = (
    " This is a children's story for ages 3-8: use only simple, kind, cheerful, age-appropriate words; "
    "nothing scary, rude or violent."
)


def script_constraints(genre: Optional[str], tone: Optional[str], visual_style: Optional[str]) -> str:
    """The extra requirement lines for the script writer, based on the form choices."""
    out = ""
    if is_kids(genre, tone, visual_style):
        out += KIDS_RULES
    if is_cinematic(visual_style, genre, tone):
        out += CINEMATIC_RULES
    return out
