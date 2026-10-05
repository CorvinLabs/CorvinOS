"""Collision-safe temp paths for Video Producer workers.

Every worker that builds intermediate files (narration audio, screenshots)
keyed them by a hand-built f"/tmp/{job_id}_{kind}_{index}.ext" string. That
is deterministic BY DESIGN — which is exactly the problem: every E2E test in
this package calls create_job(job_id="test_...") with a FIXED id, not the
UUID the default generates, so two processes using the same fixed job_id
(a parallel pytest run, a retried job, two jobs built from the same template)
write to the exact same path and clobber each other's in-flight file.

job_scoped_dir() fixes this at the primitive instead of patching each call
site separately (the same bug class recurred in 5 files — see CONCEPT note
in the fix commit): it hands each (job_id, process) pair a directory from
tempfile.mkdtemp(), whose uniqueness is guaranteed by the OS, not by the
caller picking a good name. Two processes with the IDENTICAL job_id now get
two DIFFERENT directories; one process reusing the same job_id across calls
keeps reusing its own directory, so a job's own phases still share one place.
"""

import re
import tempfile
import threading

# The id becomes part of a path; validated HERE so every caller is covered,
# including workers called directly with a SimpleNamespace job.
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_lock = threading.Lock()
_job_dirs: dict[str, str] = {}


def job_scoped_dir(job_id: str) -> str:
    """Return this process's unique temp directory for job_id.

    Same job_id, same process, repeated calls -> same directory.
    Same job_id, different process -> a different directory (the fix).
    """
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise ValueError(f"unsafe job id {job_id!r}: must match [A-Za-z0-9_-]{{1,64}}")
    with _lock:
        existing = _job_dirs.get(job_id)
        if existing is not None:
            return existing
        new_dir = tempfile.mkdtemp(prefix=f"video_{job_id}_")
        _job_dirs[job_id] = new_dir
        return new_dir


def scene_path(job_id: str, kind: str, index: int, suffix: str) -> str:
    """Collision-safe path for a scene-indexed intermediate file.

    kind distinguishes file roles within one job (e.g. "scene", "espeak",
    "screenshot") the same way the old f"/tmp/{job_id}_{kind}_{index}{suffix}"
    names did — only the directory moved, the naming scheme did not.
    """
    return f"{job_scoped_dir(job_id)}/{kind}_{index}{suffix}"
