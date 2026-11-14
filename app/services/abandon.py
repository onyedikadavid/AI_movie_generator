"""
Who is asking? (a tiny, dependency-free helper)

`RunControl.call()` runs a blocking call (like "wait for the video server's clip") in a helper thread and walks away
from it the moment the run is paused / stopped / replaced. Before this module the abandoned thread just kept polling
the video server - sometimes for an hour - and the notebook's queue filled with jobs nobody was waiting for.

Now the helper thread carries two facts, readable from anywhere below it (the video service, for instance):
  * `abandoned()`  - True once the run walked away. A polling loop stops itself.
  * `group()`      - the project id. Sent to the video server so it can drop that project's OLD, still-queued jobs
                     when a new request arrives (the old request was replaced; a GPU hour must not be spent on it).
"""
from __future__ import annotations

import threading
from typing import Optional

_local = threading.local()


def bind(event: Optional[threading.Event], group: str = "") -> None:
    _local.event = event
    _local.group = group or ""


def abandoned() -> bool:
    ev = getattr(_local, "event", None)
    return bool(ev is not None and ev.is_set())


def group() -> str:
    return getattr(_local, "group", "") or ""
