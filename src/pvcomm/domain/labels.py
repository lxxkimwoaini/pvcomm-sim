"""Oracle label contract. No detection feature is authorized by this schema."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Nanoseconds = Annotated[int, Field(strict=True, ge=0)]


class LabelContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class EvidenceRef(LabelContract):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    tier: Literal["M1", "M2", "Oracle"]
    record_id: str | None = None
    available_at: Nanoseconds | None = None
    role: Literal["online", "retrospective"]

    @model_validator(mode="after")
    def safe_reference(self) -> Self:
        if self.path.startswith(("/", "\\")) or "\\" in self.path or ":" in self.path:
            raise ValueError("evidence must be a portable run-relative path")
        if any(p in ("", ".", "..") for p in self.path.split("/")):
            raise ValueError("unsafe evidence reference")
        if self.role == "online" and (self.tier == "Oracle" or self.available_at is None):
            raise ValueError("online evidence requires an actual M1/M2 availability time")
        return self


class ServiceFinding(LabelContract):
    finding_id: str
    kind: Literal["command", "reception_freshness"]
    station_id: str
    task_id: str | None = None
    ioa: int | None = None
    outcome: str
    service_violation: bool | None
    label_status: Literal["valid", "uncertain", "invalid"]
    start_utc_ns: Nanoseconds
    end_utc_ns: Nanoseconds | None
    observable_at_utc_ns: Nanoseconds | None
    recovery_utc_ns: Nanoseconds | None
    recovery_censored: bool
    evidence: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def causal_interval(self) -> Self:
        if self.end_utc_ns is not None and self.end_utc_ns <= self.start_utc_ns:
            raise ValueError("label interval must be nonempty and half open")
        if self.service_violation:
            if self.observable_at_utc_ns is None or self.observable_at_utc_ns < self.start_utc_ns:
                raise ValueError("violation cannot be observable before its constraint deadline")
            for ref in self.evidence:
                if ref.role == "online" and (
                    ref.available_at is None or ref.available_at > self.observable_at_utc_ns
                ):
                    raise ValueError("future evidence cannot support online determination")
        if self.recovery_censored != (self.recovery_utc_ns is None):
            raise ValueError("recovery and censoring must agree")
        if self.label_status == "valid" and self.service_violation is None:
            raise ValueError("unknown result must retain uncertainty")
        return self


class EventLabel(LabelContract):
    label_version: Literal["0.4-label.2"] = "0.4-label.2"
    run_id: str
    anomaly_id: str
    station_id: str
    region_id: str
    scope: Literal["station", "region", "global"]
    fault_applied: bool | None
    fault_type: tuple[str, ...]
    fault_start_utc_ns: Nanoseconds | None
    fault_end_utc_ns: Nanoseconds | None
    transport_impact: bool | None
    communication_impact: bool | None = None
    communication_impact_start_utc_ns: Nanoseconds | None = None
    communication_impact_end_utc_ns: Nanoseconds | None = None
    recovery_complete_utc_ns: Nanoseconds | None = None
    recovery_censored: bool = True
    service_violation: bool | None
    observable_anomaly: bool | None
    operating_context: tuple[str, ...]
    label_status: Literal["valid", "uncertain", "invalid"]
    findings: tuple[ServiceFinding, ...]
    evidence: tuple[EvidenceRef, ...]
    observation_mode: Literal["M2"] = "M2"
    independent_information_available: Literal[False] = False
    uncertainty_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent_layers(self) -> Self:
        if self.recovery_censored != (self.recovery_complete_utc_ns is None):
            raise ValueError("full recovery and censoring must agree")
        if self.recovery_complete_utc_ns is not None and (
            self.fault_end_utc_ns is None or self.recovery_complete_utc_ns < self.fault_end_utc_ns
        ):
            raise ValueError("full recovery must follow actual restoration")
        for start, end in (
            (self.fault_start_utc_ns, self.fault_end_utc_ns),
            (self.communication_impact_start_utc_ns, self.communication_impact_end_utc_ns),
        ):
            if start is not None and end is not None and end <= start:
                raise ValueError("diagnostic interval must be nonempty and half open")
        if any(f.station_id != self.station_id for f in self.findings):
            raise ValueError("cross-station finding")
        actual = (
            True
            if any(f.service_violation is True for f in self.findings)
            else None
            if any(f.service_violation is None for f in self.findings)
            else False
        )
        if self.label_status != "invalid" and self.service_violation != actual:
            raise ValueError("service layer must follow business evidence, not injection")
        if self.observable_anomaly is True and self.service_violation is not True:
            raise ValueError("M2 anomaly needs an independently evidenced business violation")
        if self.label_status == "valid" and self.service_violation is None:
            raise ValueError("unknown service state cannot be valid")
        return self
