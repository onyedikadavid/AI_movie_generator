"""
Run control: everything that decides WHO is running, WHO is waiting, and how a
run is stopped, paused, resumed, cancelled or deleted.

The problems this solves
------------------------
* "Lots of projects say 'Generating...' at once and I can't tell which one is
  really running."  Before, a project showed an active status whenever its task
  had EVER started - even if the worker had been killed hours ago. Now:
    - only ONE project can run at a time (a database lease, safe across
      several worker processes / machines);
    - everything else is QUEUED with a visible position in line;
    - a running project proves it is alive with a heartbeat every few seconds;
      a project whose heartbeat stops is detected and marked, not left
      "Generating..." forever.
* Pause / resume / cancel / delete must work while a long GPU call is in
  flight. The worker's heartbeat thread notices the request within a few
  seconds, and `RunControl.call()` abandons the in-flight request at once.
* A duplicate / stale Celery message must never start a second copy of the
  same project: every run owns a unique `run_token`, and a task whose token
  no longer matches simply exits.

All functions here are SYNCHRONOUS (psycopg2). The API calls them through
`asyncio.to_thread`.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db_safety import safe_rollback
from app.core.sync_db import SessionLocal
from app.models.lease import PipelineLease
from app.models.project import ACTIVE_STATUSES, BUSY_STATUSES, Project, ProjectStatus
from app.models.scene import Scene

logger = logging.getLogger(__name__)

HOLDER = f"{socket.gethostname()}:{os.getpid()}"


def now() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Stop signalling (worker side)
# ===========================================================================
class RunStopped(Exception):
    """Raised inside a run when the user (or a newer run) asked it to stop."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "pause" | "cancel" | "delete" | "superseded"


# ===========================================================================
# The single-slot lease
# ===========================================================================
def ensure_lease_row(db: Session) -> None:
    db.execute(text("INSERT INTO pipeline_lease (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))
    db.commit()


def acquire_lease(db: Session, project_id: str, holder: str = HOLDER) -> bool:
    """Atomic compare-and-swap: take the lease if it's free, already ours, or
    its holder has stopped heart-beating (dead worker)."""
    stale_before = now() - timedelta(seconds=settings.STALE_RUN_SECONDS)
    res = db.execute(
        update(PipelineLease)
        .where(
            PipelineLease.id == 1,
            or_(
                PipelineLease.project_id.is_(None),
                PipelineLease.holder == holder,
                PipelineLease.heartbeat_at.is_(None),
                PipelineLease.heartbeat_at < stale_before,
            ),
        )
        .values(project_id=project_id, holder=holder, heartbeat_at=now())
    )
    db.commit()
    return res.rowcount == 1


def release_lease(db: Session, holder: str = HOLDER) -> None:
    try:
        safe_rollback(db)
        db.execute(
            update(PipelineLease)
            .where(PipelineLease.id == 1, PipelineLease.holder == holder)
            .values(project_id=None, holder=None, heartbeat_at=None)
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Couldn't release the pipeline lease (it will expire on its own).")
        safe_rollback(db)


def claim(db: Session, project_id: str, token: str, new_status: ProjectStatus) -> bool:
    """QUEUED -> running, but only if this task's token is still the current one."""
    res = db.execute(
        update(Project)
        .where(
            Project.id == project_id,
            Project.run_token == token,
            Project.status == ProjectStatus.QUEUED,
            Project.delete_requested.is_(False),
        )
        .values(status=new_status, started_at=now(), heartbeat_at=now(), error_message=None)
    )
    db.commit()
    return res.rowcount == 1


# ===========================================================================
# RunControl: heartbeat + stop detection + interruptible blocking calls
# ===========================================================================
class RunControl:
    def __init__(self, project_id: str, token: str, holder: str = HOLDER):
        self.project_id = project_id
        self.token = token
        self.holder = holder
        self.stop_reason: Optional[str] = None
        self._stop = threading.Event()
        self._end = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._beat()  # first beat synchronously, so flags are known immediately
        self._thread = threading.Thread(target=self._loop, name=f"heartbeat-{self.project_id[:8]}", daemon=True)
        self._thread.start()

    def end(self) -> None:
        self._end.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._end.wait(settings.HEARTBEAT_SECONDS):
            self._beat()

    def _beat(self) -> None:
        try:
            with SessionLocal() as s:
                row = s.execute(
                    select(
                        Project.pause_requested, Project.cancel_requested,
                        Project.delete_requested, Project.run_token,
                    ).where(Project.id == self.project_id)
                ).first()
                if row is None:
                    self._request_stop("delete")
                elif row.run_token != self.token:
                    self._request_stop("superseded")
                elif row.delete_requested:
                    self._request_stop("delete")
                elif row.cancel_requested:
                    self._request_stop("cancel")
                elif row.pause_requested:
                    self._request_stop("pause")

                s.execute(
                    update(Project)
                    .where(Project.id == self.project_id, Project.run_token == self.token)
                    .values(heartbeat_at=now())
                )
                s.execute(
                    update(PipelineLease)
                    .where(PipelineLease.id == 1, PipelineLease.holder == self.holder)
                    .values(heartbeat_at=now())
                )
                s.commit()
        except Exception as e:  # noqa: BLE001 - a flaky DB must never kill the run
            logger.warning("heartbeat failed (will retry): %s", e)

    def _request_stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
            logger.info("Run %s asked to stop: %s", self.project_id, reason)
        self._stop.set()

    # -- used by the pipeline -----------------------------------------------
    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def checkpoint(self) -> None:
        if self._stop.is_set():
            raise RunStopped(self.stop_reason or "pause")

    def call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Run a blocking call (an HTTP request to a GPU server, TTS, ffmpeg...) in
        a helper thread and wait for it - but give up the moment a stop is
        requested, instead of being stuck for minutes inside it. The abandoned
        call finishes (or fails) harmlessly in the background; everything it
        writes is atomic, so it can't leave a corrupt file behind.
        """
        self.checkpoint()
        box: "queue.Queue[tuple]" = queue.Queue(maxsize=1)

        def runner() -> None:
            try:
                box.put(("ok", fn(*args, **kwargs)))
            except BaseException as e:  # noqa: BLE001 - delivered to the caller
                box.put(("err", e))

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        while True:
            try:
                kind, value = box.get(timeout=0.5)
                break
            except queue.Empty:
                if self._stop.is_set():
                    raise RunStopped(self.stop_reason or "pause")
        if kind == "err":
            raise value
        return value


# ===========================================================================
# Worker-side helpers: finishing a stopped run
# ===========================================================================
def project_dir(project_id: str) -> str:
    return os.path.join(settings.STORAGE_DIR, "projects", project_id)


def delete_project_everywhere(db: Session, project_id: str) -> None:
    safe_rollback(db)
    obj = db.get(Project, project_id)
    if obj is not None:
        db.delete(obj)  # ORM cascade removes script / characters / scenes
    db.execute(
        update(PipelineLease).where(PipelineLease.project_id == project_id)
        .values(project_id=None, holder=None, heartbeat_at=None)
    )
    db.commit()
    shutil.rmtree(project_dir(project_id), ignore_errors=True)


def finish_stopped(db: Session, project_id: str, token: str, reason: str) -> None:
    """Called by the worker after a RunStopped: settle the project in its final state."""
    safe_rollback(db)
    if reason == "superseded":
        return  # a newer run owns this project now - don't touch it
    if reason == "delete":
        delete_project_everywhere(db, project_id)
        return
    if reason == "cancel":
        values: Dict[str, Any] = dict(status=ProjectStatus.CANCELLED, error_message="Cancelled by user.",
                                      stage_detail="Cancelled", cancel_requested=False, pause_requested=False)
    else:
        values = dict(status=ProjectStatus.PAUSED, error_message=None, stage_detail="Paused",
                      pause_requested=False, cancel_requested=False)
    db.execute(update(Project).where(Project.id == project_id, Project.run_token == token).values(**values))
    db.commit()


def finish_failed(db: Session, project_id: str, token: str, message: str) -> None:
    safe_rollback(db)
    db.execute(
        update(Project)
        .where(Project.id == project_id, Project.run_token == token)
        .values(status=ProjectStatus.FAILED, error_message=message[:6000], stage_detail="Failed",
                pause_requested=False, cancel_requested=False)
    )
    db.commit()


def finish_interrupted(db: Session, project_id: str, token: str) -> None:
    """The worker process itself is being shut down (Ctrl+C / SIGTERM)."""
    safe_rollback(db)
    db.execute(
        update(Project)
        .where(Project.id == project_id, Project.run_token == token)
        .values(status=ProjectStatus.PAUSED, error_message=None,
                stage_detail="Paused because the worker was shut down - press Resume to continue",
                pause_requested=False, cancel_requested=False)
    )
    db.commit()


# ===========================================================================
# Dead-run detection (used by the API on every list/get, and by the worker at start-up)
# ===========================================================================
def reconcile_stale(db: Session) -> int:
    """
    Any project that CLAIMS to be running but whose heartbeat has stopped is a
    dead run. Settle it honestly instead of showing 'Generating...' forever.
    Returns how many were fixed.
    """
    cutoff = now() - timedelta(seconds=settings.STALE_RUN_SECONDS)
    fixed = 0
    rows = db.execute(select(Project).where(Project.status.in_(ACTIVE_STATUSES))).scalars().all()
    for p in rows:
        last = p.heartbeat_at or p.started_at
        if last is not None and last >= cutoff:
            continue  # alive
        logger.warning("Project %s looked active but its worker went quiet - settling it.", p.id)
        if p.delete_requested:
            delete_project_everywhere(db, p.id)
            fixed += 1
            continue
        if p.cancel_requested:
            p.status, p.error_message, p.stage_detail = ProjectStatus.CANCELLED, "Cancelled by user.", "Cancelled"
        elif p.pause_requested:
            p.status, p.error_message, p.stage_detail = ProjectStatus.PAUSED, None, "Paused"
        else:
            p.status = ProjectStatus.FAILED
            p.stage_detail = "Worker stopped responding"
            p.error_message = (
                "The worker stopped responding in the middle of this run (it was closed, restarted, "
                "or lost its connection). Everything that was already finished is saved - "
                "press Resume to continue from where it stopped."
            )
        p.pause_requested = p.cancel_requested = False
        fixed += 1
    if fixed:
        db.commit()
    # A lease whose holder went quiet is simply free.
    db.execute(
        update(PipelineLease)
        .where(PipelineLease.id == 1, PipelineLease.project_id.is_not(None),
               or_(PipelineLease.heartbeat_at.is_(None), PipelineLease.heartbeat_at < cutoff))
        .values(project_id=None, holder=None, heartbeat_at=None)
    )
    db.commit()
    return fixed


def recover_orphans_on_worker_start(db: Session) -> None:
    """When a worker starts, a lease still held by an OLD process of this same
    machine can only be a leftover from a crash: free it and settle its project
    right away rather than waiting for the heartbeat to time out."""
    host = socket.gethostname()
    lease = db.get(PipelineLease, 1)
    if lease is None or not lease.holder or not lease.project_id:
        return
    # NOTE: deliberately no "is that process still alive?" probe - on Windows
    # os.kill(pid, 0) would KILL the process. The heartbeat is the evidence: a
    # live worker beats every few seconds, so >20s of silence means it's gone.
    quiet_for = (now() - lease.heartbeat_at).total_seconds() if lease.heartbeat_at else 1e9
    if lease.holder.startswith(f"{host}:") and lease.holder != HOLDER and quiet_for > max(20, 4 * settings.HEARTBEAT_SECONDS):
        pid = lease.project_id
        logger.warning("Recovering project %s left over from a previous worker process (%s).", pid, lease.holder)
        db.execute(update(Project).where(Project.id == pid).values(heartbeat_at=now() - timedelta(days=1)))
        db.execute(update(PipelineLease).where(PipelineLease.id == 1).values(heartbeat_at=now() - timedelta(days=1)))
        db.commit()
        reconcile_stale(db)


# ===========================================================================
# API-side actions
# ===========================================================================
class ActionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_START_FROM = {
    ProjectStatus.SCRIPT_READY, ProjectStatus.FAILED, ProjectStatus.CANCELLED,
    ProjectStatus.PAUSED, ProjectStatus.COMPLETED,
}
_RESUME_FROM = {ProjectStatus.PAUSED, ProjectStatus.FAILED, ProjectStatus.CANCELLED}


def _load(db: Session, project_id: str) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise ActionError(404, "Project not found.")
    return p


def _enqueue(db: Session, p: Project, kind: str, fresh: bool) -> Project:
    from app.tasks.pipeline_tasks import run_asset_pipeline, run_script_breakdown  # lazy: avoids a circular import

    had_progress = (p.progress_pct or 0) > 0 or p.status in (ProjectStatus.PAUSED, ProjectStatus.FAILED, ProjectStatus.CANCELLED)
    p.run_token = uuid.uuid4().hex
    p.status = ProjectStatus.QUEUED
    p.queued_at = now()
    p.started_at = None
    p.heartbeat_at = None
    p.error_message = None
    p.warning_message = None
    p.pause_requested = p.cancel_requested = p.delete_requested = False
    p.stage_detail = "Waiting for its turn"
    if kind == "assets":
        scenes = db.execute(select(Scene).where(Scene.project_id == p.id)).scalars().all()
        for sc in scenes:
            if fresh:
                sc.render_status = "REDO"
            elif sc.render_status != "DONE":
                sc.render_status = "PENDING"
        p.total_scenes = len(scenes)
        if fresh:
            p.progress_pct, p.current_scene, p.final_video_path = 0, None, None
        if had_progress and not fresh:
            p.resume_count = (p.resume_count or 0) + 1
        elif fresh:
            p.resume_count = 0
    else:
        p.progress_pct, p.current_scene, p.total_scenes = 0, None, None
    db.commit()

    try:
        task = (run_script_breakdown if kind == "script" else run_asset_pipeline).delay(p.id, p.run_token)
    except Exception as e:  # noqa: BLE001 - Redis/broker unreachable
        logger.error("Couldn't queue project %s: %s", p.id, e)
        p.status, p.run_token, p.stage_detail = ProjectStatus.FAILED, None, "Couldn't be queued"
        p.error_message = (
            "Couldn't hand this job to the worker queue (is Redis / Upstash reachable?). "
            f"Nothing was lost - press Resume to try again. Details: {e}"
        )
        db.commit()
        raise ActionError(503, "Couldn't reach the job queue (Redis). Please try again in a moment.") from e
    p.celery_task_id = task.id
    db.commit()
    return p


def start_new_script(db: Session, p: Project) -> Project:
    """Used right after project creation (the project row already exists)."""
    return _enqueue(db, p, "script", fresh=False)


def start_generation(db: Session, project_id: str, fresh: Optional[bool] = None) -> Project:
    p = _load(db, project_id)
    if p.status in BUSY_STATUSES:
        raise ActionError(409, f"Project is already {'queued' if p.status in (ProjectStatus.QUEUED, ProjectStatus.CREATED) else 'running'}.")
    if p.status not in _START_FROM:
        raise ActionError(409, f"Project isn't ready for generation yet (status: {p.status.value}).")
    has_scenes = db.execute(select(Scene.id).where(Scene.project_id == p.id).limit(1)).first() is not None
    if not has_scenes:
        return _enqueue(db, p, "script", fresh=False)
    return _enqueue(db, p, "assets", fresh=bool(fresh))


def resume(db: Session, project_id: str) -> Project:
    p = _load(db, project_id)
    if p.status not in _RESUME_FROM:
        raise ActionError(409, f"Nothing to resume (status: {p.status.value}).")
    has_scenes = db.execute(select(Scene.id).where(Scene.project_id == p.id).limit(1)).first() is not None
    return _enqueue(db, p, "assets" if has_scenes else "script", fresh=False)


def pause(db: Session, project_id: str) -> Project:
    p = _load(db, project_id)
    if p.delete_requested:
        raise ActionError(409, "This project is being deleted.")
    if p.status in (ProjectStatus.QUEUED, ProjectStatus.CREATED):
        # Not started yet: park it immediately. Clearing the token makes the
        # queued Celery message a no-op when the worker eventually sees it.
        p.status, p.run_token, p.stage_detail = ProjectStatus.PAUSED, None, "Paused"
        db.commit()
    elif p.status in ACTIVE_STATUSES:
        p.pause_requested = True
        p.stage_detail = "Pausing - finishing the current step"
        db.commit()
    else:
        raise ActionError(409, f"Nothing to pause (status: {p.status.value}).")
    return p


def cancel(db: Session, project_id: str) -> Project:
    p = _load(db, project_id)
    if p.delete_requested:
        raise ActionError(409, "This project is being deleted.")
    if p.status in (ProjectStatus.QUEUED, ProjectStatus.CREATED, ProjectStatus.PAUSED):
        p.status, p.run_token, p.error_message, p.stage_detail = ProjectStatus.CANCELLED, None, "Cancelled by user.", "Cancelled"
        db.commit()
    elif p.status in ACTIVE_STATUSES:
        p.cancel_requested = True
        p.pause_requested = False
        p.stage_detail = "Cancelling - finishing the current step"
        db.commit()
    else:
        raise ActionError(409, f"Nothing to cancel (status: {p.status.value}).")
    return p


def delete(db: Session, project_id: str) -> Dict[str, bool]:
    """Idle project -> deleted now. Running project -> asked to stop and delete
    itself within a few seconds (the UI shows 'Deleting...')."""
    p = _load(db, project_id)
    if p.status in ACTIVE_STATUSES:
        p.delete_requested = True
        p.stage_detail = "Deleting - stopping the current step"
        db.commit()
        return {"deleted": False, "deleting": True}
    delete_project_everywhere(db, project_id)
    return {"deleted": True, "deleting": False}


# ===========================================================================
# Queue snapshot (for the dashboard)
# ===========================================================================
def queued_ids_in_order(db: Session) -> List[str]:
    rows = db.execute(
        select(Project.id, Project.queued_at, Project.created_at)
        .where(Project.status.in_((ProjectStatus.QUEUED, ProjectStatus.CREATED)))
    ).all()
    rows.sort(key=lambda r: (r.queued_at or r.created_at or now(), r.id))
    return [r.id for r in rows]


def queue_snapshot(db: Session) -> Dict[str, Any]:
    running = db.execute(
        select(Project).where(Project.status.in_(ACTIVE_STATUSES)).order_by(Project.started_at.asc().nullslast())
    ).scalars().first()
    order = queued_ids_in_order(db)
    titles = {}
    if order:
        for pid, title, raw, queued_at, created_at in db.execute(
            select(Project.id, Project.title, Project.raw_prompt, Project.queued_at, Project.created_at).where(Project.id.in_(order))
        ).all():
            titles[pid] = (title or (raw or "Untitled project")[:60], queued_at or created_at)

    def brief(p: Project) -> Dict[str, Any]:
        return {
            "id": p.id,
            "title": p.title or (p.raw_prompt or "Untitled project")[:60],
            "status": p.status.value,
            "progress_pct": p.progress_pct or 0,
            "stage_detail": p.stage_detail,
            "current_scene": p.current_scene,
            "total_scenes": p.total_scenes,
            "pause_requested": bool(p.pause_requested),
            "cancel_requested": bool(p.cancel_requested),
            "delete_requested": bool(p.delete_requested),
            "heartbeat_age_seconds": (now() - p.heartbeat_at).total_seconds() if p.heartbeat_at else None,
        }

    oldest_age = None
    if order:
        oldest_age = max(0.0, (now() - titles[order[0]][1]).total_seconds()) if titles[order[0]][1] else None
    return {
        "running": brief(running) if running else None,
        "queued": [{"id": pid, "title": titles[pid][0], "position": i + 1} for i, pid in enumerate(order)],
        "oldest_queued_age_seconds": oldest_age,
    }
