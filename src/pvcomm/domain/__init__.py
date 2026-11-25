"""Shared types with standard-library dependencies only."""

from enum import StrEnum


class ObservationMode(StrEnum):
    M0 = "M0"
    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    ORACLE = "Oracle"


class RunStatus(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    QUEUED = "queued"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QCStatus(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
