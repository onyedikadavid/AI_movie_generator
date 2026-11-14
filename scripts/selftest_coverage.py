"""
Self-test for camera coverage / eye-lines:  python scripts/selftest_coverage.py
Two people talking are seen from the side (two-shot, then over-the-shoulder), each looking at the other and never into
the lens; a lone speaker is seen in three-quarter view; a deliberate direct address keeps the old wording.
"""
import os, sys
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.config import settings  # noqa: E402
from app.services import prompt_builder as pb  # noqa: E402

V = NS(name="Vesper", appearance_prompt="a woman in her mid-30s, short dark hair, green eyes", description="")
K = NS(name="Kiran", appearance_prompt="a young man, spiky black hair, scar above his left eyebrow", description="")
OLD = "changing angle and framing to follow whoever is speaking"


def beat(who, speech, action="stands attentively", kind="dialogue"):
    return {"kind": kind, "speaker": who, "speech": speech, "action": action, "expression": "firm", "voice_desc": ""}


def prompt(beats, cast, motion="Slow dolly-in on Vesper's face, then a handheld pan to the ancient artifacts",
           vis="A dim archive chamber with tall shelves"):
    return pb.whole_scene_prompt(beats, cast, "The archive", vis, "cinematic", motion, "distant whispers")


def main():
    settings.CONVERSATION_COVERAGE = True
    settings.CONVERSATION_STAGING = "stay"

    # 1. two people: side-on two-shot, over-the-shoulder, eye-lines, no lens
    beats = [beat("Vesper", "Kiran, what do these markings mean?"), beat("Kiran", "I have never seen them before.")]
    p = prompt(beats, [V, K])
    assert "face to face" in p and "seen from the side" in p, p
    assert "over-the-shoulder" in p and "never straight in front of a speaker" in p, p
    assert "turned toward Kiran, eyes on Kiran" in p and "turned toward Vesper, eyes on Vesper" in p, p
    assert "nobody ever looks into the camera lens" in p, p
    assert OLD not in p, "the old 'follow whoever is speaking' wording must be gone"
    assert "dolly-in on Vesper's face" not in p and "handheld pan to the ancient artifacts" in p, p
    print("OK 1: two people talking -> side-on two-shot, over-the-shoulder, eyes on each other, no frontal dolly-in")

    # 2. who is addressed
    on = [V, K]
    assert pb.addressee("Vesper", "Kiran, look at this", on) == "Kiran"                    # name in the line
    assert pb.addressee("Vesper", "Look at this", on, [beat("Kiran", "Hello"), beat("Vesper", "Look at this")], 1) == "Kiran"  # a reply
    assert pb.addressee("Vesper", "Look at this", [V, K, NS(name="Morvath", appearance_prompt="")], [beat("Vesper", "Look at this"), beat("Morvath", "Yes")], 0) == "Morvath"  # next speaker
    assert pb.addressee("Vesper", "Look at this", [V, K]) == "Kiran"                        # the only other person
    assert pb.addressee("Vesper", "Look at this", [V]) == ""                                # someone off-frame
    assert pb.addressee("Kiran", "Chancellor Morvath, hear me", [K, NS(name="Grand Chancellor Morvath", appearance_prompt="")]) == "Grand Chancellor Morvath"
    print("OK 2: the person being spoken to is found (name in the line, reply, next speaker, only other person)")

    # 3. a lone speaker: three-quarter view, listeners just off-frame
    p = prompt([beat("Jack", "People of the town, listen to me!")], [NS(name="Jack", appearance_prompt="a young man, dark hair")],
               motion="Slow push-in on his face")
    assert "three-quarter view" in p and "never into the camera lens" in p and "never straight in front of the speaker" in p, p
    assert "push-in on his face" not in p, p
    assert "eyes on the listener just off-frame, not on the camera" in p, p
    print("OK 3: a lone speaker (a crowd, someone off-frame) -> seen from the side, eyes off the lens, no face-on push-in")

    # 4. a deliberate direct address keeps the camera in front
    p = prompt([beat("Jack", "Welcome back to my channel, speaking to the camera today")], [NS(name="Jack", appearance_prompt="")], motion="Slow push-in on his face")
    assert OLD in p and "three-quarter" not in p, p
    print("OK 4: direct address to the camera keeps the old, frontal wording")

    # 5. switch off -> the old prompt exactly
    settings.CONVERSATION_COVERAGE = False
    p = prompt(beats, [V, K])
    assert OLD in p and "face to face" not in p and "turned toward" not in p, p
    settings.CONVERSATION_COVERAGE = True
    print("OK 5: CONVERSATION_COVERAGE=false restores the old prompt")

    # 6. motion filter
    assert pb.strip_frontal_motion("Slow dolly-in on Vesper's face, then a handheld pan to the ancient artifacts") == "a handheld pan to the ancient artifacts"
    assert pb.strip_frontal_motion("extreme close-up of his eyes") == ""
    assert pb.strip_frontal_motion("tracking shot following her down the corridor") == "tracking shot following her down the corridor"
    print("OK 6: only the face-on parts of the script's camera note are dropped")

    # 7. walking conversation keeps the tracking camera but nobody looks into the lens
    settings.CONVERSATION_STAGING = "walk"
    p = prompt([beat("Vesper", "Kiran, we must hurry."), beat("Kiran", "I know.")], [V, K], vis="A long street at dawn", motion="")
    assert "tracking shots" in p and "nobody looks into the camera lens" in p, p
    print("OK 7: walk-and-talk keeps its tracking camera, eyes stay on each other")
    settings.CONVERSATION_STAGING = "stay"

    # 8. pictures: keyframe, close-up and the negative prompt
    kf = pb.keyframe_prompt("Medium shot of Kiran in an alley", [V, K], "cinematic")
    assert "face each other in profile" in kf, kf
    assert "eyes turned away from the camera" in pb.keyframe_prompt("Jack in a church", [NS(name="Jack", appearance_prompt="")], "cinematic")
    cu = pb.closeup_prompt(K, "firm", "an alley", "cinematic", True)
    assert "not looking at the camera" in cu and "natural relaxed pose" not in cu, cu
    assert "looking at the camera" in pb.image_negative("cinematic")
    settings.CONVERSATION_COVERAGE = False
    assert "looking at the camera" not in pb.image_negative("cinematic") and "natural relaxed pose" in pb.closeup_prompt(K, "firm", "an alley", "cinematic", True)
    settings.CONVERSATION_COVERAGE = True
    print("OK 8: keyframes, close-ups and the negative prompt also keep people from staring into the lens")
    print("ALL COVERAGE CHECKS PASSED")


if __name__ == "__main__":
    main()
