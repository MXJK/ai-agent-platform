from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    session_storage: str = "memory"
    persistent_sessions: bool = False


class TimingMetricResponse(BaseModel):
    count: int
    total_ms: int
    max_ms: int
    average_ms: float


class MetricsResponse(BaseModel):
    service: str
    counters: dict[str, int] = Field(default_factory=dict)
    gauges: dict[str, int] = Field(default_factory=dict)
    timings: dict[str, TimingMetricResponse] = Field(default_factory=dict)
