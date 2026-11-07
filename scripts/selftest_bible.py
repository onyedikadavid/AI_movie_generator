"""Story bible: places and characters are matched, described once and reused. No database or GPU needed.
Run from this folder:  python scripts/selftest_bible.py"""
import os, sys, uuid
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.services import bible, prompt_builder as pb  # noqa: E402


def scene(n, loc, vis="", cont=False):
    return NS(scene_number=n, location=loc, location_id=None, continues_previous=cont, visual_description=vis or f"scene {n}",
              image_prompt=f"image {n}")


def char(name, look):
    return NS(name=name, description="", appearance_prompt=look, core_look=None, outfit=None, outfits=None)


def maker(store):
    def make(key, name):
        l = NS(id=str(uuid.uuid4()), key=key, name=name, description="", reference_image_path=None)
        store.append(l)
        return l
    return make


asked = []


def fake_llm(prompt):
    asked.append(prompt)
    import re
    keys = re.findall(r'- key "([^"]+)"', prompt)
    return {
        "places": [{"key": k, "description": f"The {k.replace('-', ' ')}: wooden benches, tools on the wall, warm light"} for k in keys],
        "characters": [
            {"name": "Jack", "core_look": "30-year-old man, round face, short black hair", "outfit": "blue shirt and jeans",
             "outfits": {"jack-carpenter-workshop": "brown leather apron", "mechanic-workshop": "grey oily overalls", "bogus": "x"}},
            {"name": "Mark", "core_look": "35-year-old man, beard, tall", "outfit": "green jacket", "outfits": {}},
        ],
    }


def main():
    chars = [char("Jack", "Jack, 30, round face, blue shirt"), char("Mark", "Mark, 35, beard, green jacket"), char("Neth", "Neth, 28, braids")]
    scenes = [
        scene(1, "Jack's house"),
        scene(2, "Jack's carpenter workshop", "Jack saws wood."),
        scene(3, "Carpenter Workshop - morning", "Mark visits."),
        scene(4, "Mechanic workshop"),
        scene(5, "The mechanic's garage", "Jack fixes a car."),
        scene(6, "workshop", "Mark advises him."),
        scene(7, "", cont=True),
        scene(8, "Cobbler shop"),
        scene(9, "Cobbler's Workshop (evening)"),
    ]
    locs = []
    out = bible.apply_bible(scenes, chars, locs, maker(locs), "cartoon", "funny", fake_llm)
    ids = [s.location_id for s in scenes]
    assert out["places"] == 4, out
    assert ids[1] == ids[2], "same carpenter workshop must be ONE place"
    assert ids[3] == ids[4] == ids[5] == ids[6], "mechanic garage/workshop + 'workshop' + a continuing scene stay in the mechanic workshop"
    assert ids[7] == ids[8], "cobbler shop = cobbler's workshop"
    assert len({ids[0], ids[1], ids[3], ids[7]}) == 4
    assert all(l.description for l in locs)
    assert len(asked) == 1, "one language-model call for the whole bible"
    jack = chars[0]
    assert jack.core_look.startswith("30-year-old") and "bogus" not in jack.outfits
    assert "apron" in bible.character_look(jack, "jack-carpenter-workshop")
    assert "overalls" in bible.character_look(jack, "mechanic-workshop")
    assert "blue shirt" in bible.character_look(jack, "cobbler-workshop"), "default outfit elsewhere"
    assert jack.core_look in bible.character_look(jack, "cobbler-workshop"), "the face never changes"
    neth = chars[2]
    assert neth.core_look == "Neth, 28, braids" and bible.character_look(neth, "x") == "Neth, 28, braids", "no model data: look kept as written"

    # Continue the story: a scene back in the carpenter workshop REUSES the entry; nothing existing is rewritten.
    before = {l.key: l.description for l in locs}
    asked.clear()
    new = scene(10, "Carpenter workshop at night")
    scenes.append(new)
    out = bible.apply_bible(scenes, chars, locs, maker(locs), "cartoon", "funny", fake_llm)
    assert new.location_id == ids[1] and out["new_places"] == 0 and not asked, (out, asked)
    assert before == {l.key: l.description for l in locs}
    new2 = scene(11, "Baker's bakery")
    scenes.append(new2)
    bible.apply_bible(scenes, chars, locs, maker(locs), "cartoon", "funny", fake_llm)
    assert new2.location_id not in ids and len(locs) == 5 and locs[-1].description, "a new place gets its own entry"

    # No language model: still built from the script's own text, never raises.
    locs2 = []
    sc2 = [scene(1, "Cobbler shop", "A tiny shop full of shoes.")]
    ch2 = [char("Jack", "Jack, 30")]
    def boom(_):
        raise RuntimeError("ollama down")
    bible.apply_bible(sc2, ch2, locs2, maker(locs2), "", "", boom)
    assert locs2[0].description and ch2[0].core_look == "Jack, 30"

    # Rendering side: the characters as they look in THIS place, the place text on the scene.
    class Loc(NS): pass
    lm = {l.id: Loc(**vars(l)) for l in locs}
    view = bible.BibleView(list(lm.values()), chars)
    sc = scenes[1]
    cs = view.prepare(sc)
    jack_here = next(c for c in cs if c.name == "Jack")
    assert "leather apron" in jack_here.appearance_prompt and sc.place_key == "jack-carpenter-workshop"
    assert sc.place_text.startswith("Jack's carpenter workshop:") and sc.place_desc
    kf = pb.keyframe_prompt("Jack saws wood", cs[:2], "cartoon", sc.place_desc)
    assert "set in" in kf and "wooden benches" in kf, kf
    assert "leather apron" in kf or "round face" in kf
    # the same place -> the same description in the keyframe of another scene there
    sc3 = scenes[2]; view.prepare(sc3)
    assert sc3.place_desc == sc.place_desc and sc3.place_key == sc.place_key

    # A place description must never trigger body-situation staging (tools "lying" on a bench is not a person lying down).
    tricky = "Workshop: tools lying on the bench, a chair to sit on, a bed in the corner"
    p = pb.text_video_prompt("dialogue", "Jack", "calm", "", "Jack, 30", [NS(name="Jack"), NS(name="Mark")], tricky,
                             "Jack talks to Mark", "cartoon", 0, "auto", None, False, speech="Hello there", model_voice=True,
                             dialogue_text="Hello there")
    assert "walk side by side" in p and "lies " not in p and "lying down" not in p, p
    print("selftest_bible OK -", out)


if __name__ == "__main__":
    main()
