"""
Image-first workflow (v2.31): every scene's opening picture is made BEFORE any video, shown to the user, and may be
redrawn (optionally with the user's own prompt) or taken from another scene. The approved picture is the first frame of
that scene's long clip.

The approved pictures live in  <project>/images/scene_<n>.png  - OUTSIDE the scene folders, because a scene folder is
wiped whenever the picture changes, and these files are the user's choice.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from typing import Optional

from app.core.config import settings
from app.services import prompt_builder as pb
from app.services.media_storage import publish_file
from app.services.scene_renderer import SceneRenderer, _seed
from app.services.style_presets import resolve_style

logger = logging.getLogger(__name__)


def images_dir(project_id: str) -> str:
    return os.path.join(settings.STORAGE_DIR, "projects", project_id, "images")


def approved_path(project_id: str, scene_number: int) -> str:
    return os.path.join(images_dir(project_id), f"scene_{int(scene_number)}.png")


def _nonempty(path: str) -> bool:
    return bool(path) and os.path.exists(path) and os.path.getsize(path) > 0


def _context(db, project):
    """(bible view, style) for drawing this project's pictures."""
    from sqlalchemy import select   # imported here so the file-only helpers above work without a database
    from app.models.character import Character
    from app.models.location import Location
    from app.models.script import Script
    from app.services.bible import BibleView
    characters = db.execute(select(Character).where(Character.project_id == project.id)).scalars().all()
    locations = db.execute(select(Location).where(Location.project_id == project.id)).scalars().all()
    script = db.execute(select(Script).where(Script.project_id == project.id)).scalars().first()
    style = resolve_style(project.requested_visual_style, script.visual_style if script else None,
                          project.requested_genre, project.requested_tone)
    return BibleView(locations, characters), style


def _publish(project_id: str, scene_number: int, path: str) -> str:
    # A new name every time: the old picture may be cached by the CDN / the browser.
    return publish_file(path, f"projects/{project_id}/images/scene_{scene_number}_{uuid.uuid4().hex[:8]}.png")


def draw(image_service, scene, scene_chars, style: str, project_id: str, prompt: Optional[str] = None, variant: int = 0) -> str:
    """Draws the scene's opening picture and stores it as the scene's approved picture. `prompt` (the user's own words)
    replaces the scene's picture description; the cast's looks, the place and the style are still added around it."""
    on_screen = SceneRenderer._characters_on_screen(scene, scene_chars)
    base = (prompt or "").strip() or scene.image_prompt or scene.visual_description
    kp = pb.keyframe_prompt(base, on_screen, style, getattr(scene, "place_desc", ""))
    seed = _seed(project_id, getattr(scene, "place_key", "") or scene.scene_number, "wide") + 7919 * int(variant)
    dest = approved_path(project_id, scene.scene_number)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part.png"
    extra = {}
    if settings.BIBLE_PICTURES:
        # The people in this scene are shown to the image model as their bible pictures, and the picture starts from the
        # place's own picture, so faces and rooms come out the same every time.
        from app.services import bible_assets
        speakers = [t.get("speaker") for t in (getattr(scene, "dialogue_turns", None) or []) if isinstance(t, dict)]
        refs, place = bible_assets.references_for(project_id, on_screen, getattr(scene, "place_id", None), speakers)
        if refs:
            extra["references"] = refs
        if place:
            extra["init_image"] = place
    image_service.generate_image(kp, tmp, pb.image_negative(style), None, None, seed, **extra)
    os.replace(tmp, dest)
    return dest


def draw_missing(db, project: Project, image_service, report=None, checkpoint=None) -> int:
    """Draws every scene's picture that does not exist yet (a resumed run keeps the ones already made). Returns how
    many were drawn. `report(i, total, scene)` is called before each picture, `checkpoint()` between them."""
    from sqlalchemy import select
    from app.models.scene import Scene
    bible, style = _context(db, project)
    scenes = db.execute(select(Scene).where(Scene.project_id == project.id).order_by(Scene.scene_number)).scalars().all()
    drawn = 0
    for i, scene in enumerate(scenes):
        if checkpoint:
            checkpoint()
        path = approved_path(project.id, scene.scene_number)
        if _nonempty(path) and scene.image_path:
            continue
        if report:
            report(i, len(scenes), scene)
        if not _nonempty(path):
            chars = bible.prepare(scene)
            draw(image_service, scene, chars, style, project.id)
            drawn += 1
        scene.image_path = _publish(project.id, scene.scene_number, path)
        db.commit()
    return drawn


def regenerate(db, project: Project, scene: Scene, image_service, prompt: Optional[str] = None,
               from_scene: Optional[Scene] = None) -> Scene:
    """One scene's picture again: drawn from `prompt` (or the scene's own description), or copied from another scene."""
    dest = approved_path(project.id, scene.scene_number)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if from_scene is not None:
        src = approved_path(project.id, from_scene.scene_number)
        if not _nonempty(src):
            raise ValueError(f"Scene {from_scene.scene_number} has no picture yet.")
        if os.path.abspath(src) != os.path.abspath(dest):
            shutil.copyfile(src, dest)
    else:
        bible, style = _context(db, project)
        chars = bible.prepare(scene)
        # A different picture each time the user presses it, even with the same words.
        draw(image_service, scene, chars, style, project.id, prompt=prompt, variant=int(time.time()) % 100000)
    if (prompt or "").strip():
        scene.image_prompt = prompt.strip()      # remembered: the next full redraw starts from the user's words
    scene.image_path = _publish(project.id, scene.scene_number, dest)
    scene.render_status = "PENDING"              # the clip made from the old picture is out of date
    db.commit()
    return scene
