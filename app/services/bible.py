"""The story bible: one canonical entry per CHARACTER and per PLACE of a project.

Why: the script writer re-describes a place or a person every time it comes up, so "the carpenter's workshop" in scene 2
and scene 5 end up as two different rooms, and Jack's face and clothes drift. The bible fixes that:

* Places - every scene's free-text `location` is matched to ONE Location row (same workshop = same row, however the
  writer worded it). Each row has one saved description of the PLACE, reused in every scene set there, plus a seed.
* Characters - a `core_look` that never changes (face, hair, skin, build, age) and outfits per place, so Jack is the same
  man in the carpenter's and the cobbler's workshop, in the right clothes.

`ensure_bible` is idempotent and ONLY fills in what is missing: entries that already exist (or that the user edited) are
never rewritten, and a scene that comes back to an existing place simply reuses it. It never raises: if the language model
is unavailable the bible is still built from the script's own text.
"""
from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Words that say WHEN or HOW a place is seen, not WHICH place it is.
_NOISE = {
    "the", "a", "an", "of", "in", "at", "on", "inside", "outside", "interior", "exterior", "int", "ext", "near", "with",
    "and", "morning", "afternoon", "evening", "night", "dawn", "dusk", "day", "later", "next", "same", "scene", "back",
    "day", "midday", "noon", "sunset", "sunrise", "early", "late", "still", "again", "now", "that", "this", "his", "her",
    "their", "s", "small", "big", "old", "busy", "empty", "quiet", "dark", "bright",
}


# Different words for the same kind of place ("cobbler shop" = "cobbler's workshop").
_KIND = {w: "workshop" for w in ("workshop", "shop", "garage", "studio", "store", "stall", "forge", "atelier")}
_KIND.update({w: "house" for w in ("house", "home", "apartment", "flat", "residence", "cottage")})
_KIND.update({w: "street" for w in ("street", "road", "lane", "alley", "avenue", "path")})
_KIND.update({w: "market" for w in ("market", "marketplace", "bazaar")})


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def place_tokens(name: str) -> frozenset:
    """The words that identify a place: "Jack's carpenter workshop - morning" -> {jack, carpenter, workshop}."""
    text = re.sub(r"[’']s\b", "", (name or "").lower())
    words = re.findall(r"[a-z0-9]+", text)
    return frozenset(_KIND.get(_stem(w), _stem(w)) for w in words if w not in _NOISE and len(w) > 1)


def slugify(name: str) -> str:
    text = re.sub(r"[\u2019']s\b", "", (name or "").lower())
    out: list = []
    for w in re.findall(r"[a-z0-9]+", text):
        if w in _NOISE or len(w) < 2:
            continue
        t = _KIND.get(_stem(w), _stem(w))
        if t not in out:
            out.append(t)
    return "-".join(out) or re.sub(r"[^a-z0-9]+", "-", (name or "place").lower()).strip("-") or "place"


def same_place(a: frozenset, b: frozenset, strict_single: bool = False) -> bool:
    """True when two location names are one place. Close wording (>= 60% shared words) or one name containing the other
    ("carpenter workshop" / "Jack's carpenter workshop") counts; "carpenter workshop" vs "mechanic workshop" does not."""
    if not a or not b:
        return False
    inter = len(a & b)
    if inter == 0:
        return False
    if a == b:
        return True
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if inter / len(a | b) >= 0.6:
        return True
    return len(small) >= 2 and small <= big


def match_location(name: str, known: Sequence[tuple]) -> Optional[str]:
    """`known` = [(key, tokens), ...] oldest first -> the key of the place `name` refers to, or None for a new place.
    A one-word name ("workshop") means the most recent place that has that word in it."""
    toks = place_tokens(name)
    if not toks:
        return None
    if len(toks) == 1:
        for key, kt in reversed(list(known)):
            if toks <= kt:
                return key
        return None
    for key, kt in known:
        if same_place(toks, kt):
            return key
    return None


# --------------------------------------------------------------------------------------------------------------------
# Resolving what a scene should use
# --------------------------------------------------------------------------------------------------------------------
def character_look(c, loc_key: str = "") -> str:
    """What this character looks like in a scene set at `loc_key`: the fixed core look + the outfit for that place."""
    core = (getattr(c, "core_look", None) or "").strip().rstrip(",.")
    outfits = getattr(c, "outfits", None) or {}
    outfit = ((outfits.get(loc_key) if loc_key else None) or getattr(c, "outfit", None) or "").strip().rstrip(",.")
    if core and outfit:
        return f"{core}, wearing {outfit}"
    return (getattr(c, "appearance_prompt", None) or core or "").strip()


class BibleView:
    """Used while rendering: gives every scene its place text and the characters as they look THERE."""

    def __init__(self, locations: Sequence, characters: Sequence):
        self.by_id = {l.id: l for l in locations}
        self.characters = list(characters)

    def prepare(self, scene) -> list:
        loc = self.by_id.get(getattr(scene, "location_id", None))
        key = loc.key if loc else ""
        # Transient attributes (not columns): read by the renderer.
        scene.place_key = key
        scene.place_text = f"{loc.name}: {loc.description}".strip(": ").strip() if (loc and (loc.description or "").strip()) else (loc.name if loc else "")
        scene.place_desc = (loc.description or "").strip() if loc else ""
        out = []
        for c in self.characters:
            table = getattr(c, "__table__", None)
            data = {col.name: getattr(c, col.name) for col in table.columns} if table is not None else dict(vars(c))
            data["appearance_prompt"] = character_look(c, key)
            out.append(SimpleNamespace(**data))
        return out


# --------------------------------------------------------------------------------------------------------------------
# Building the bible
# --------------------------------------------------------------------------------------------------------------------
def _first_text(scenes: Sequence, limit: int = 2) -> str:
    return " | ".join((s.visual_description or "").strip()[:160] for s in list(scenes)[:limit] if (s.visual_description or "").strip())


def _prompt(style: str, tone: str, chars: Sequence, places: Sequence[dict]) -> str:
    cl = "\n".join(f"        - {c.name}: {c.description or ''} | LOOK: {c.appearance_prompt or ''}" for c in chars) or "        (none)"
    pl = "\n".join(f'        - key "{p["key"]}": {p["name"]} (seen in: {p["seen"] or "n/a"})' for p in places) or "        (none)"
    keys = ", ".join(f'"{p["key"]}"' for p in places)
    return f"""
        You are the continuity supervisor of a film. Write the production BIBLE so that every scene looks the same
        every time a place or a person comes back. Visual style: {style or 'cinematic'}. Tone: {tone or 'any'}.

        CHARACTERS:
{cl}

        PLACES:
{pl}

        For EACH place write "description": 30 to 45 words describing the PLACE ONLY - layout, key furniture and tools,
        materials, colours, light, time of day. Concrete and filmable, no people, no action, no story.
        For EACH character write:
          "core_look": what NEVER changes - age, face, skin, hair, build (max 25 words, no clothing),
          "outfit": their usual clothing (max 18 words),
          "outfits": {{place key: clothing THERE}} ONLY for places where the story has them dressed differently
                      (e.g. a leather apron in the carpenter's workshop). Allowed place keys: {keys or 'none'}.
        Keep every detail the characters' LOOK above already states; never contradict it.

        Return JSON exactly like this:
        {{
            "places": [{{"key": "...", "description": "..."}}],
            "characters": [{{"name": "...", "core_look": "...", "outfit": "...", "outfits": {{}}}}]
        }}
        Respond ONLY with raw JSON.
        """


def apply_bible(scenes: Sequence, chars: Sequence, locs: Sequence, make_location: Callable[[str, str], object],
                style: str = "", tone: str = "", ask_llm: Optional[Callable[[str], dict]] = None) -> dict:
    """The bible logic on plain objects (no database): assigns every scene a place (creating places with
    `make_location(key, name)` when needed) and fills in missing descriptions / core looks. Existing entries are kept."""
    by_key = {l.key: l for l in locs}
    by_id = {l.id: l for l in locs}
    known = [(l.key, place_tokens(l.name)) for l in locs]
    seen_in: Dict[str, list] = {}
    new_keys: List[str] = []

    prev_loc = None
    for sc in scenes:
        loc = by_id.get(sc.location_id) if sc.location_id else None
        if loc is None:
            name = (sc.location or "").strip()
            if not name and getattr(sc, "continues_previous", False) and prev_loc is not None:
                loc = prev_loc          # "carries on" with no place named: same place as the scene before
            elif name:
                key = match_location(name, known)
                if key is None:
                    base = slugify(name)
                    key, n = base, 2
                    while key in by_key:
                        key, n = f"{base}-{n}", n + 1
                    loc = make_location(key, name.strip(" -.,:;")[:80] or key)
                    by_key[key], by_id[loc.id] = loc, loc
                    known.append((key, place_tokens(loc.name)))
                    new_keys.append(key)
                else:
                    loc = by_key[key]
            if loc is not None:
                sc.location_id = loc.id
        if loc is not None:
            seen_in.setdefault(loc.key, []).append(sc)
            prev_loc = loc

    todo_places = [l for l in by_key.values() if not (l.description or "").strip()]
    todo_chars = [c for c in chars if not (c.core_look or "").strip()]
    data: dict = {}
    if ask_llm and (todo_places or todo_chars):
        try:
            prompt = _prompt(style, tone, todo_chars, [
                {"key": l.key, "name": l.name, "seen": _first_text(seen_in.get(l.key, []))} for l in todo_places])
            data = ask_llm(prompt) or {}
        except Exception as e:  # noqa: BLE001 - the bible must never block the film
            logger.warning("Story bible: the language model could not write it (%s) - using the script's own text.", e)
            data = {}
    pdesc = {str(p.get("key")): str(p.get("description") or "").strip()
             for p in (data.get("places") or []) if isinstance(p, dict)}
    cdata = {str(c.get("name", "")).strip().lower(): c for c in (data.get("characters") or []) if isinstance(c, dict)}

    for l in todo_places:
        desc = pdesc.get(l.key, "")
        if not desc:  # fallback: what the script itself said about this place
            first = (seen_in.get(l.key) or [None])[0]
            desc = (getattr(first, "image_prompt", "") or getattr(first, "visual_description", "") or "")[:220].strip() if first else ""
        l.description = desc
    valid = set(by_key)
    for c in todo_chars:
        d = cdata.get(c.name.strip().lower()) or {}
        core, outfit = str(d.get("core_look") or "").strip(), str(d.get("outfit") or "").strip()
        outs = d.get("outfits") if isinstance(d.get("outfits"), dict) else {}
        if core and outfit:
            c.core_look, c.outfit = core, outfit
            c.outfits = {k: str(v).strip() for k, v in outs.items() if k in valid and str(v).strip()} or None
        else:
            c.core_look = (c.appearance_prompt or "").strip()   # no split available: the look stays as it was written
    return {"places": len(by_key), "new_places": len(new_keys), "characters": len(chars)}


def ensure_bible(db, project_id: str, style: str = "", tone: str = "", ask_llm: Optional[Callable[[str], dict]] = None) -> dict:
    """Database wrapper around `apply_bible`. Only missing entries are created. Never raises."""
    from sqlalchemy import select
    from app.models.character import Character
    from app.models.location import Location
    from app.models.scene import Scene

    def make(key: str, name: str):
        loc = Location(project_id=project_id, key=key, name=name, description="")
        db.add(loc)
        db.flush()
        return loc

    try:
        scenes = db.execute(select(Scene).where(Scene.project_id == project_id).order_by(Scene.scene_number)).scalars().all()
        chars = db.execute(select(Character).where(Character.project_id == project_id)).scalars().all()
        locs = list(db.execute(select(Location).where(Location.project_id == project_id)).scalars().all())
        out = apply_bible(scenes, chars, locs, make, style, tone, ask_llm)
        db.commit()
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("Story bible could not be built: %s", e)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"places": 0, "new_places": 0, "characters": 0, "error": str(e)}
