"""실행 API 응답 모델."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from datespot_agent.models import CamelModel, RunConfig


class RunJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunAccepted(CamelModel):
    run_id: str
    status: RunJobStatus
    status_url: str
    report_url: str


class RunStatusResponse(CamelModel):
    run_id: str
    status: RunJobStatus
    config: RunConfig
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report_available: bool = False
    error: str | None = None


class HealthResponse(CamelModel):
    status: Literal["ok"] = "ok"
    accepting: bool
    active_run_id: str | None = None
    queued_runs: int = 0
