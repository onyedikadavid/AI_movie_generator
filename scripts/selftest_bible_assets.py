"""
Self-test: bible pictures (v2.32). Characters and places are drawn once; every scene picture is then built from them.
  1. a character picture is drawn from the character's look (name, gender, outfit), one person alone, portrait size
  2. a place picture is drawn without people
  3. a scene picture is given the bible pictures of the people IN the scene (not of absent ones) and of its place
  4. the user's own words become the saved look; scenes showing that character are reported as out of date
  5. a server that cannot use references (no picture on disk yet) simply draws from the text
Run:   python scripts/selftest_bible_assets.py
"""
import os, shutil, sys, tempfile
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PIL import Image  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import bible_assets as ba, scene_images  # noqa: E402


class Img:
    def __init__(self):
        self.calls = []

    def generate_image(self, prompt, path, negative="", width=None, height=None, seed=None, **kw):
        self.calls.append(dict(prompt=prompt, neg=negative, w=width, h=height, seed=seed, **kw))
        Image.new("RGB", (width or 1216, height or 832), (90, 90, 90)).save(path)
        return path


def main():
    tmp = tempfile.mkdtemp(prefix="bible_")
    try:
        settings.STORAGE_DIR = tmp
        settings.BIBLE_PICTURES = True
        settings.BIBLE_COMPOSE = False      # the older IP-Adapter way is checked first; the cut-out way below
        mike = NS(id="c1", name="Michael", description="a short boy", appearance_prompt="short boy, blonde hair", core_look="short boy, blonde hair, freckles",
                  outfit="a red shirt", gender="male", age_group="child")
        obi = NS(id="c2", name="Obinna", description="a tall man", appearance_prompt="tall man", core_look=None, outfit=None, gender="male", age_group="adult")
        hut = NS(id="l1", name="Village", description="a village of small wooden houses with a hut in the middle", key="village")
        img = Img()
        p = ba.draw_character(img, mike, "Cinematic", "p1")
        c = img.calls[-1]
        assert p == ba.character_path("p1", "c1") and os.path.getsize(p) > 0
        assert "Michael" in c["prompt"] and "blonde" in c["prompt"] and "red shirt" in c["prompt"] and "boy" in c["prompt"] or "child" in c["prompt"], c["prompt"]
        assert "one person only" in c["prompt"] and c["h"] > c["w"], c
        print("OK 1: character picture - one person, portrait, name/look/outfit in the prompt")

        ba.draw_place(img, hut, "Cinematic", "p1")
        c = img.calls[-1]
        assert "wooden houses" in c["prompt"] and "no people" in c["prompt"] and "people" in c["neg"], c
        assert os.path.getsize(ba.place_path("p1", "l1")) > 0
        print("OK 2: place picture - empty of people")

        ba.draw_character(img, obi, "Cinematic", "p1")
        scene = NS(scene_number=1, image_prompt="Michael runs through the village", visual_description="", characters_present=["Michael"],
                   place_id="l1", place_key="village", place_desc=hut.description)
        img2 = Img()
        scene_images.draw(img2, scene, [mike, obi], "Cinematic", "p1")
        c = img2.calls[-1]
        assert c["references"] == [ba.character_path("p1", "c1")], c.get("references")     # only Michael, not Obinna
        assert c["init_image"] == ba.place_path("p1", "l1"), c
        print("OK 3: the scene picture gets the pictures of the people in it and of its place")

        # nobody listed -> everybody
        scene.characters_present = []
        scene_images.draw(img2, scene, [mike, obi], "Cinematic", "p1")
        assert len(img2.calls[-1]["references"]) == 2
        # no picture drawn yet for a person -> not sent
        os.remove(ba.character_path("p1", "c2"))
        scene_images.draw(img2, scene, [mike, obi], "Cinematic", "p1")
        assert img2.calls[-1]["references"] == [ba.character_path("p1", "c1")]
        os.remove(ba.place_path("p1", "l1"))
        scene_images.draw(img2, scene, [mike, obi], "Cinematic", "p1")
        assert "init_image" not in img2.calls[-1]
        print("OK 5: missing pictures are left out, the scene is drawn from the text")

        settings.BIBLE_PICTURES = False
        scene_images.draw(img2, scene, [mike, obi], "Cinematic", "p1")
        assert "references" not in img2.calls[-1]
        settings.BIBLE_PICTURES = True

        scenes = [NS(id="s1", scene_number=1, characters_present=["Michael"], location_id="l1"),
                  NS(id="s2", scene_number=2, characters_present=["Obinna"], location_id="l2"),
                  NS(id="s3", scene_number=3, characters_present=["Michael", "Obinna"], location_id="l1")]
        aff = ba.scenes_with_character(scenes, [mike, obi], mike)
        assert [s.id for s in aff] == ["s1", "s3"], aff
        assert [s.id for s in ba.scenes_in_place(scenes, hut)] == ["s1", "s3"]
        prompt = ba.character_prompt(mike, "Cinematic", prompt="short boy with golden curls")
        assert "golden curls" in prompt and "freckles" not in prompt
        print("OK 4: the user's words replace the look; the scenes that show the character are found")
        # ---- v2.34: the scene picture is the EMPTY place + the bible characters cut out of their own pictures
        settings.BIBLE_COMPOSE = True

        class Img2(Img):
            def __init__(self, can=True):
                super().__init__(); self.can, self.cuts, self.composed = can, [], []

            def cutout(self, src, dst):
                if not self.can:
                    return False
                self.cuts.append(os.path.basename(src)); Image.new("RGBA", (50, 100), (1, 2, 3, 255)).save(dst); return True

            def compose_scene(self, bg, people, out, prompt="", negative="", refine=0.0, seed=None, width=None, height=None):
                if not self.can:
                    return False
                self.composed.append((os.path.basename(bg), [os.path.basename(p) for p, _ in people], [round(h, 2) for _, h in people], prompt))
                Image.new("RGB", (64, 64)).save(out); return True

        ba.draw_character(Img(), mike, "Cinematic", "p1"); ba.draw_character(Img(), obi, "Cinematic", "p1"); ba.draw_place(Img(), hut, "Cinematic", "p1")
        scene.characters_present = ["Michael"]
        scene.dialogue_turns = [{"speaker": "Michael", "text": "hi"}]
        i3 = Img2()
        out = scene_images.draw(i3, scene, [mike, obi], "Cinematic", "p1")
        bg, names, heights, kp = i3.composed[-1]
        assert bg == "place_l1.png", bg                                   # the empty place, shared by every scene set there
        assert names == ["character_c1_cut.png"], names                   # only Michael, from his own bible picture
        assert heights == [0.44 * 1.0], heights                           # a child stands shorter than a grown-up
        assert not any("scene" in c["prompt"] and "Michael" in c["prompt"] and "wearing" in c["prompt"] for c in i3.calls), "nobody is drawn inside the scene"
        assert os.path.getsize(out) > 0
        print("OK 6: scene picture = the empty place + Michael cut out of his bible picture (no one drawn inside the scene)")
        scene.place_id = None
        i4 = Img2()
        scene_images.draw(i4, scene, [mike, obi], "Cinematic", "p1")
        assert i4.calls and "no people" in i4.calls[0]["prompt"], "a scene without a known place gets its own EMPTY place"
        assert i4.composed[-1][0] == "environment_scene1.png"
        scene_images.draw(i4, scene, [mike, obi], "Cinematic", "p1", prompt="a stone battlefield at dawn")
        assert "battlefield" in i4.calls[-1]["prompt"] and "no people" in i4.calls[-1]["prompt"]
        print("OK 7: a different place per scene; the user's words draw an empty place for that scene")
        old = Img2(can=False)
        scene_images.draw(old, scene, [mike, obi], "Cinematic", "p1")
        # v2.35.5: an older notebook no longer needs the IP-Adapter way - the built-in cut-out + composite make the picture
        assert os.path.getsize(scene_images.approved_path("p1", scene.scene_number)) > 0
        print("OK 8: an older notebook still works (built-in cut-out and composite)")
        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
