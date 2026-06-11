"""Background job tracking for long computations.

POST /api/jobs returns immediately with a job id; the browser polls for
stage/progress and can cancel. Long work runs in daemon threads so Ctrl-C
shuts the server down promptly instead of waiting on a download.

Cancellation is cooperative: the provider checks the event between stages.
An in-flight OSM download can't be aborted mid-request, but its result still
lands in the cache, so a cancelled-then-retried query is fast.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .providers import AreaTooLargeError, JobCancelled

FINISHED_JOB_TTL_S = 600


@dataclass
class Job:
    id: str
    status: str = "running"  # running | done | error | cancelled
    stage: str = "Starting"
    progress: float = 0.0  # coarse 0..1
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict | None = None
    error: dict | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "elapsed_s": round((self.finished_at or time.time()) - self.started_at, 1),
            "result": self.result if self.status == "done" else None,
            "error": self.error,
        }


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _gc() -> None:
    now = time.time()
    for jid in [
        j.id
        for j in _jobs.values()
        if j.finished_at and now - j.finished_at > FINISHED_JOB_TTL_S
    ]:
        del _jobs[jid]


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def cancel(job_id: str) -> Job | None:
    job = get(job_id)
    if job and job.status == "running":
        job.cancel_event.set()
        job.stage = "Cancelling"
    return job


def start(work: Callable[[Job], dict]) -> Job:
    """Run ``work(job)`` in a daemon thread; its return value becomes the result."""
    job = Job(id=uuid.uuid4().hex[:12])
    with _lock:
        _gc()
        _jobs[job.id] = job

    def run() -> None:
        try:
            result = work(job)
            if job.cancel_event.is_set():
                job.status = "cancelled"
            else:
                job.result = result
                job.progress = 1.0
                job.status = "done"
        except JobCancelled:
            job.status = "cancelled"
        except AreaTooLargeError as exc:
            job.status = "error"
            job.error = {
                "reason": "area_too_large",
                "message": str(exc),
                "radius_km": round(exc.radius_km, 1),
                "hosted_available": exc.hosted_available,
            }
        except Exception as exc:  # surface anything else to the UI
            job.status = "error"
            job.error = {"reason": "internal", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            job.finished_at = time.time()

    threading.Thread(target=run, daemon=True, name=f"roam-job-{job.id}").start()
    return job
