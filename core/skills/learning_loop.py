"""Learning Loop — @skill_learnable decorator + async grading (ADR-0306)."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable, TypeVar

from core.concurrency.queue import Queue
from core.context.helpers import get_current_context

from .skill import Grade, Skill

_T = TypeVar("_T")

# Global grading queue (ADR-0304 Queue primitive)
_GRADING_QUEUE: Queue[dict[str, Any]] | None = None


def init_grading_queue(queue: Queue[dict[str, Any]]) -> None:
    """Initialize the global grading queue (call once at boot)."""
    global _GRADING_QUEUE
    _GRADING_QUEUE = queue


def skill_learnable(
    name: str,
    version: str = "1.0",
    tags: list[str] | None = None,
    tier: str = "bundled",
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorator to make a callable learnable (captures invocation metadata).

    Usage:
        @skill_learnable(name="my-skill", version="1.0", tags=["tool-use"])
        def my_skill(x: int) -> str:
            return f"result: {x}"

    At runtime:
    - Captures: (args, output, elapsed, exception)
    - Queues async grading request
    - Returns original result (non-blocking)
    """
    if not name or "/" in name:
        raise ValueError(f"Skill name invalid: {name!r}")

    tags = tags or []

    def decorator(func: Callable[..., _T]) -> Callable[..., _T]:
        skill_obj = Skill(
            name=name,
            version=version,
            body=func.__code__.co_filename,  # File path as proxy
            tags=tags,
            tier=tier,
        )

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> _T:
            start_t = time.time()
            exception = None
            output = None

            try:
                output = func(*args, **kwargs)
                return output
            except Exception as e:
                exception = e
                raise
            finally:
                elapsed = time.time() - start_t
                _queue_grading_request(
                    skill_obj=skill_obj,
                    args=args,
                    kwargs=kwargs,
                    output=output,
                    elapsed=elapsed,
                    exception=exception,
                )

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> _T:
            start_t = time.time()
            exception = None
            output = None

            try:
                output = await func(*args, **kwargs)
                return output
            except Exception as e:
                exception = e
                raise
            finally:
                elapsed = time.time() - start_t
                _queue_grading_request(
                    skill_obj=skill_obj,
                    args=args,
                    kwargs=kwargs,
                    output=output,
                    elapsed=elapsed,
                    exception=exception,
                )

        # Choose wrapper based on whether func is async
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        else:
            return sync_wrapper  # type: ignore

    return decorator


def _queue_grading_request(
    skill_obj: Skill,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    output: Any | None,
    elapsed: float,
    exception: Exception | None,
) -> None:
    """Queue a grading request asynchronously (non-blocking)."""
    if _GRADING_QUEUE is None:
        return  # Grading not initialized, silently skip

    try:
        ctx = get_current_context()
    except (RuntimeError, ValueError):
        ctx = {}

    request = {
        "skill_name": skill_obj.name,
        "skill_version": skill_obj.version,
        "args": str(args)[:100],  # Limit arg capture to 100 chars
        "kwargs": str(kwargs)[:100],
        "output": str(output)[:100] if output else None,
        "elapsed": elapsed,
        "exception": type(exception).__name__ if exception else None,
        "context": ctx,
        "timestamp": time.time(),
    }

    try:
        _GRADING_QUEUE.put(request)
    except Exception:
        pass  # Queue full — drop request silently (non-blocking guarantee)


class SkillLearningManager:
    """Manages skill learning loop: capture → grade → persist."""

    def __init__(self, store: Any):  # store: SkillStore
        self.store = store
        self.grading_queue: Queue[dict[str, Any]] = Queue(maxsize=1000)
        init_grading_queue(self.grading_queue)

    def register_skill(self, skill: Skill) -> None:
        """Store a skill in the learning system."""
        self.store.save(skill)

    async def run_grading_loop(self, grader_fn: Callable[[dict[str, Any]], Grade | None]) -> None:
        """Run the async grading loop.

        Pulls requests from grading_queue and applies grader_fn.
        Persists grades back to skills in store.
        """
        while True:
            request = self.grading_queue.get(blocking=False)
            if not request:
                await asyncio.sleep(0.1)
                continue

            name = request.get("skill_name")
            version = request.get("skill_version")

            if not name or not version:
                continue

            skill = self.store.load(name, version)
            if not skill:
                continue

            grade = await grader_fn(request)
            if grade:
                skill.add_grade(grade)
                self.store.save(skill)

    def get_skill(self, name: str, version: str) -> Skill | None:
        """Retrieve a skill."""
        return self.store.load(name, version)

    def list_top_skills(self, limit: int = 10) -> list[Skill]:
        """Get top-performing skills by mean_score."""
        return self.store.list_by_mean_score(limit=limit)
