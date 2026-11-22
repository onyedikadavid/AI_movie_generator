"""Picks the music bed: one of the user's own tracks in MUSIC_DIR. A track whose file name contains a word of the
project's tone / genre (e.g. "sad_piano.mp3" for a sad story) wins; otherwise the choice is stable per project."""
import hashlib
import os
import re
from typing import Optional

from app.core.config import settings

_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")


def music_dir() -> str:
    return settings.MUSIC_DIR or os.path.join(settings.STORAGE_DIR, "music")


def pick_track(project_id: str, *hints: Optional[str]) -> Optional[str]:
    folder = music_dir()
    try:
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith(_EXT))
    except OSError:
        return None
    if not files:
        return None
    words = {w for h in hints for w in re.findall(r"[a-z]{3,}", (h or "").lower())}
    for f in files:
        if words & set(re.findall(r"[a-z]{3,}", os.path.splitext(f)[0].lower())):
            return os.path.join(folder, f)
    idx = int(hashlib.sha1(project_id.encode()).hexdigest(), 16) % len(files)
    return os.path.join(folder, files[idx])
