"""Cron automation for continuous Skill loss-trigger polling.

Components:
- CronTriggerPoller: Detects loss signals and emits alerts
- CronService: Manages scheduled polling with APScheduler

ADR-0613: Loss signals feed autonomous forge loop.
"""

from .cron_trigger_poller import CronTriggerPoller, LossTrigger
from .cron_service import (
    CronService,
    get_cron_service,
    start_cron_service,
    stop_cron_service,
)

__all__ = [
    "CronTriggerPoller",
    "LossTrigger",
    "CronService",
    "get_cron_service",
    "start_cron_service",
    "stop_cron_service",
]
