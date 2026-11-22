"""
Bible pictures (v2.32): before any scene picture or video is made, every CHARACTER and every PLACE of the story is
drawn once. These pictures are shown to the user (who can redraw any of them, with their own words) and are then used
as the reference for every scene: the image model is shown the character's picture whenever that character is in the
scene, and starts from the place's picture whenever the scene is set there. That is what keeps Michael looking like
Michael, and the village looking like the same village, in scene 1, 2 and 3.

Files live in  <project>/bible/  - outside the scene folders, so they survive every re-render.
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import List, Optional

from app.core.config import settings
from app.services import prompt_builder as pb
from app.services.media_storage import publish_file
from app.services.scene_renderer import SceneRenderer, _seed

logger = logging.getLogger(__name__)

CHAR_W, CHAR_H = 896, 1152      # portrait, knees up: the face is big enough to be recognised
PLACE_W, PLACE_H = 1216, 832    # same landscape bucket as the scene pictures


def bible_dir(project_id: str) -> str:
    return os.path.join(settings.STORAGE_DIR, "projects", project_id, "bible")


def character_path(project_id: str, character_id: str) -> str:
    return os.path.join(bible_dir(project_id), f"character_{character_id}.png")


def cutout_path(project_id: str, character_id: str) -> str:
    return os.path.join(bible_dir(project_id), f"character_{character_id}_cut.png")


def environment_path(project_id: str, key) -> str:
    return os.path.join(bible_dir(project_id), f"environment_{key}.png")


def place_path(project_id: str, location_id: str) -> str:
    return os.path.join(bible_dir(project_id), f"place_{location_id}.png")


def _nonempty(path: str) -> bool:
    return bool(path) and os.path.exists(path) and os.path.getsize(path) > 0


def _publish(project_id: str, kind: str, item_id: str, path: str) -> str:
    return publish_file(path, f"projects/{project_id}/bible/{kind}_{item_id}_{uuid.uuid4().hex[:8]}.png")


_GENDER = {"male": "man", "female": "woman"}
_AGE = {"child": "child", "teen": "teenager", "adult": "", "elder": "elderly"}


def character_prompt(char, style: Optional[str], prompt: Optional[str] = None) -> str:
    """One person, alone, plainly lit: a clean reference to copy from. `prompt` (the user's own words) replaces the stored look."""
    look = (prompt or "").strip() or (getattr(char, "core_look", None) or "").strip() \
        or (getattr(char, "appearance_prompt", None) or "").strip() or (getattr(char, "description", None) or "").strip()
    outfit = (getattr(char, "outfit", None) or "").strip()
    if outfit and outfit.lower()[:20] not in look.lower():
        look = f"{look}, wearing {outfit}"
    who = " ".join(x for x in (_AGE.get(getattr(char, "age_group", None) or "", ""), _GENDER.get(getattr(char, "gender", None) or "", "")) if x)
    head = f"character reference picture of {char.name}" + (f", a {who}" if who else "")
    parts = [head, pb.clip_words(look, 60),
             "one person only, standing relaxed and facing the camera in three-quarter view, knees-up framing, the whole face and outfit clearly visible",
             "plain soft neutral grey studio background, even soft lighting",
             pb._style_text(style) if style else "", pb.image_style_suffix(style)]
    return ", ".join(p for p in parts if p)


def place_prompt(loc, style: Optional[str], prompt: Optional[str] = None) -> str:
    desc = (prompt or "").strip() or (getattr(loc, "description", None) or "").strip() or loc.name
    parts = [f"establishing wide shot of {loc.name}", pb.clip_words(desc, 70),
             "an empty place with no people in it, nobody in frame", "natural daylight unless the place says otherwise",
             pb._style_text(style) if style else "", pb.image_style_suffix(style)]
    return ", ".join(p for p in parts if p)


def _negative(style, extra: str) -> str:
    return pb.image_negative(style) + ", " + extra


def draw_character(image_service, char, style: str, project_id: str, prompt: Optional[str] = None, variant: int = 0) -> str:
    dest = character_path(project_id, char.id)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part.png"
    seed = _seed(project_id, "character", char.id) + 7919 * int(variant)
    image_service.generate_image(character_prompt(char, style, prompt), tmp,
                                 _negative(style, "two people, group, crowd, multiple people, cropped head, hat covering the face"),
                                 CHAR_W, CHAR_H, seed)
    os.replace(tmp, dest)
    return dest


def draw_place(image_service, loc, style: str, project_id: str, prompt: Optional[str] = None, variant: int = 0) -> str:
    dest = place_path(project_id, loc.id)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part.png"
    seed = _seed(project_id, "place", loc.id) + 7919 * int(variant)
    image_service.generate_image(place_prompt(loc, style, prompt), tmp,
                                 _negative(style, "people, person, crowd, man, woman, child, character, face"),
                                 PLACE_W, PLACE_H, seed)
    os.replace(tmp, dest)
    return dest


def height_fraction(char) -> float:
    """How tall the person stands in a scene picture, as a share of the picture height (children are shorter)."""
    desc = " ".join(str(getattr(char, a, "") or "") for a in ("description", "appearance_prompt", "core_look")).lower()
    age = (getattr(char, "age_group", None) or "").lower()
    if any(w in desc for w in ("baby", "infant", "newborn", "month-old", "toddler")):
        return 0.30
    base = {"child": 0.44, "teen": 0.58, "elder": 0.60}.get(age, 0.66)
    return base * (0.95 if (getattr(char, "gender", None) or "") == "female" else 1.0)


def ensure_cutout(image_service, project_id: str, char, force: bool = False) -> Optional[str]:
    """The character's picture without its background, made once and kept. None = the image notebook cannot do it
    (an older notebook): the caller then falls back to the older way of drawing a scene."""
    src, dst = character_path(project_id, char.id), cutout_path(project_id, char.id)
    if not _nonempty(src):
        return None
    if force and os.path.exists(dst):
        os.remove(dst)
    if _nonempty(dst):
        return dst
    tmp = dst + ".part.png"
    ok = image_service.cutout(src, tmp)
    if not ok:
        return None
    os.replace(tmp, dst)
    return dst


def draw_environment(image_service, text: str, style: str, project_id: str, key, variant: int = 0) -> str:
    """An EMPTY place for one scene (no people), from the scene's own words."""
    dest = environment_path(project_id, key)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part.png"
    fake = type("E", (), {"name": "the scene", "description": text})()
    image_service.generate_image(place_prompt(fake, style), tmp,
                                 _negative(style, "people, person, crowd, man, woman, child, character, face"),
                                 PLACE_W, PLACE_H, _seed(project_id, "environment", key) + 7919 * int(variant))
    os.replace(tmp, dest)
    return dest


def references_for(project_id: str, on_screen, place_id: Optional[str] = None, speakers=None) -> tuple[List[str], Optional[str]]:
    """The pictures a scene picture must be built from: (the people who are in it, the place it is set in).
    One image can follow only a few faces at once, so when more people are in the scene the ones who SPEAK come first;
    the rest (a crowd, passers-by) are drawn from the text."""
    spoke = {str(n).strip().lower() for n in (speakers or [])}
    ordered = sorted(list(on_screen), key=lambda c: 0 if c.name.strip().lower() in spoke else 1)
    refs: List[str] = []
    for c in ordered[: max(1, int(settings.BIBLE_REF_MAX))]:
        cid = getattr(c, "id", None)
        if cid and _nonempty(character_path(project_id, cid)):
            refs.append(character_path(project_id, cid))
    place = place_path(project_id, place_id) if place_id else None
    return refs, (place if place and _nonempty(place) else None)


def scenes_with_character(scenes, characters, character) -> List:
    """Scenes whose picture shows this character."""
    out = []
    for s in scenes:
        if any(c.id == character.id for c in SceneRenderer._characters_on_screen(s, characters)):
            out.append(s)
    return out


def scenes_in_place(scenes, location) -> List:
    return [s for s in scenes if getattr(s, "location_id", None) == location.id]


# ------------------------------------------------------------------------------------------------------ with a database
def _context(db, project):
    from sqlalchemy import select
    from app.models.character import Character
    from app.models.location import Location
    from app.models.scene import Scene
    from app.services import scene_images
    chars = db.execute(select(Character).where(Character.project_id == project.id)).scalars().all()
    locs = db.execute(select(Location).where(Location.project_id == project.id)).scalars().all()
    scenes = db.execute(select(Scene).where(Scene.project_id == project.id).order_by(Scene.scene_number)).scalars().all()
    _, style = scene_images._context(db, project)
    return chars, locs, scenes, style


def draw_missing(db, project, image_service, report=None, checkpoint=None) -> int:
    """Draws every character and place that has no picture yet (a resumed run keeps the ones already made)."""
    chars, locs, scenes, style = _context(db, project)

    def appearances(c):
        return len(scenes_with_character(scenes, chars, c))
    chars = sorted(chars, key=appearances, reverse=True)
    if int(settings.BIBLE_MAX_CHARACTERS or 0) > 0:        # optional ceiling; by default every character of the story is drawn
        chars = chars[: int(settings.BIBLE_MAX_CHARACTERS)]
    todo = [("character", c) for c in chars] + [("place", l) for l in locs]
    drawn = 0
    for i, (kind, item) in enumerate(todo):
        if checkpoint:
            checkpoint()
        path = character_path(project.id, item.id) if kind == "character" else place_path(project.id, item.id)
        if _nonempty(path) and item.reference_image_path:
            if kind == "character" and settings.BIBLE_COMPOSE:
                ensure_cutout(image_service, project.id, item)
            continue
        if report:
            report(i, len(todo), kind, item)
        if not _nonempty(path):
            (draw_character if kind == "character" else draw_place)(image_service, item, style, project.id)
            drawn += 1
        if kind == "character" and settings.BIBLE_COMPOSE:
            ensure_cutout(image_service, project.id, item)
        item.reference_image_path = _publish(project.id, kind, item.id, path)
        db.commit()
    return drawn


def regenerate_character(db, project, char, image_service, prompt: Optional[str] = None) -> List:
    """Draw this character again. The user's words become the character's saved look (so every scene uses them).
    Returns the scenes whose pictures show this character and are now out of date."""
    chars, _, scenes, style = _context(db, project)
    if (prompt or "").strip():
        char.core_look = prompt.strip()
        char.appearance_prompt = prompt.strip()
    draw_character(image_service, char, style, project.id, prompt=None, variant=int(time.time()) % 100000)
    if settings.BIBLE_COMPOSE:
        ensure_cutout(image_service, project.id, char, force=True)
    char.reference_image_path = _publish(project.id, "character", char.id, character_path(project.id, char.id))
    db.commit()
    return scenes_with_character(scenes, chars, char)


def regenerate_place(db, project, loc, image_service, prompt: Optional[str] = None) -> List:
    _, _, scenes, style = _context(db, project)
    if (prompt or "").strip():
        loc.description = prompt.strip()
    draw_place(image_service, loc, style, project.id, prompt=None, variant=int(time.time()) % 100000)
    loc.reference_image_path = _publish(project.id, "place", loc.id, place_path(project.id, loc.id))
    db.commit()
    return scenes_in_place(scenes, loc)
