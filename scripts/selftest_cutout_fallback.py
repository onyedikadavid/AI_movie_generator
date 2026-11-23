"""
Self-test (v2.35.4): a cut-out never loses the run.
 - the image notebook drops the connection / has no /v1/cutout -> the built-in cut-out makes a transparent PNG of the character
 - a stop request still stops the run
"""
import os, shutil, sys, tempfile
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PIL import Image, ImageDraw  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import bible_assets  # noqa: E402


def studio_picture(path):
    """A person on a grey studio background with a vignette, as the character prompt asks for."""
    w, h = 448, 576
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            d = ((x - w / 2) ** 2 + (y - h / 2) ** 2) ** 0.5 / 400
            g = int(150 - 40 * d)
            px[x, y] = (g, g, g + 4)
    d = ImageDraw.Draw(img)
    d.ellipse([170, 60, 280, 190], fill=(224, 172, 140))            # head
    d.rectangle([150, 190, 300, 480], fill=(180, 40, 50))            # dress
    d.rectangle([180, 480, 270, 570], fill=(60, 40, 30))             # legs
    img.save(path)


class Dropped:
    def cutout(self, s, d):
        raise RuntimeError("Cutting the character out: the image server could not be reached (ReadError('connection forcibly closed')).")


class Old:
    def cutout(self, s, d):
        return False


class RunStopped(Exception):          # same name as the app's own stop signal
    pass


class Stopper:
    def cutout(self, s, d):
        raise RunStopped("stop")


def main():
    tmp = tempfile.mkdtemp(prefix="cut_")
    try:
        settings.STORAGE_DIR = tmp
        os.makedirs(bible_assets.bible_dir("p1"), exist_ok=True)
        char = NS(id="c1", name="Ada")
        studio_picture(bible_assets.character_path("p1", "c1"))
        for label, svc in (("dropped connection", Dropped()), ("older notebook", Old())):
            dst = bible_assets.cutout_path("p1", "c1")
            if os.path.exists(dst):
                os.remove(dst)
            out = bible_assets.ensure_cutout(svc, "p1", char)
            assert out and os.path.exists(out), label
            cut = Image.open(out)
            assert cut.mode == "RGBA", cut.mode
            a = cut.getchannel("A")
            w, h = cut.size
            assert a.getpixel((w // 2, h // 3)) > 240, "the body must stay"
            assert a.getpixel((2, 2)) < 15 and a.getpixel((w - 3, 2)) < 15, "the corners must be transparent"
            assert 0.25 < w / 448 < 0.6 and 0.8 < h / 576 < 1.0, cut.size                    # cropped to the person, not the whole picture
            print(f"OK: {label} -> built-in cut-out {cut.size}")
        os.remove(bible_assets.cutout_path("p1", "c1"))
        try:
            bible_assets.ensure_cutout(Stopper(), "p1", char)
            raise AssertionError("a stop must stop")
        except Exception as e:
            assert type(e).__name__ == "RunStopped", e
        print("OK: a stop request still stops the run")
        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
