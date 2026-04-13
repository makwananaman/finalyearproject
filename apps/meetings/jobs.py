from __future__ import annotations

import threading
import uuid
from typing import Any


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def create_job(initial_state: dict[str, Any] | None = None) -> str:
    job_id = uuid.uuid4().hex
    state = {
        "status": "queued",
        "message": "Queued for processing.",
        "result": None,
        "error_message": "",
        "transcript_input": "",
    }
    if initial_state:
        state.update(initial_state)

    with _jobs_lock:
        _jobs[job_id] = state

    return job_id


def update_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(updates)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None
