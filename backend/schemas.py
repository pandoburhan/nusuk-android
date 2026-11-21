import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field
from backend.models import KeySource, QuizResultStatus, LogSeverity


class ActionResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Any] = None


# ---- Instances ----
class InstanceTestRequest(BaseModel):
    connection_type: str = Field(default="tcp", description="'tcp' | 'usb' | 'wireless'")
    ip: Optional[str] = None
    port: Optional[int] = Field(default=5555, ge=1, le=65535)
    adb_serial: Optional[str] = None


class InstancePairRequest(BaseModel):
    ip: str = Field(..., min_length=1)
    pairing_port: int = Field(..., ge=1, le=65535)
    pairing_code: str = Field(..., min_length=1, max_length=32)


class InstanceCreateRequest(BaseModel):
    name: Optional[str] = None
    connection_type: str = Field(default="tcp", description="'tcp' | 'usb' | 'wireless'")
    ip: Optional[str] = None
    port: Optional[int] = Field(default=5555, ge=1, le=65535)
    adb_serial: Optional[str] = None
    force_new_uuid: bool = False


class InstanceUpdateRequest(BaseModel):
    name: Optional[str] = None
    connection_type: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    adb_serial: Optional[str] = None
    live_preview_enabled: Optional[bool] = None


class DeviceStopCondition(BaseModel):
    stop_mode: Optional[str] = Field(default="limit", description="'limit' or 'until_exhausted'")
    stop_limit: Optional[int] = Field(default=100, ge=1)


class InstanceStartRequest(BaseModel):
    mode: str = Field(default="auto")
    stop_mode: Optional[str] = Field(default="limit")
    stop_limit: Optional[int] = Field(default=100, ge=1)


# ---- Bulk Control ----
class BulkStartRequest(BaseModel):
    device_uuids: List[str] = Field(default_factory=list)
    mode: str = Field(default="auto")
    device_stop_conditions: Optional[Any] = Field(default=None, description="List or map of per-device stop conditions")
    stop_mode: Optional[str] = Field(default="limit")
    stop_limit: Optional[int] = Field(default=100, ge=1)


class BulkStopRequest(BaseModel):
    device_uuids: List[str] = Field(default_factory=list)


class BulkRestartRequest(BaseModel):
    device_uuids: List[str] = Field(default_factory=list)
    kind: str = Field(default="app", description="'app' or 'device'")


# ---- Key Vault ----
class KeyCreateManual(BaseModel):
    code: str = Field(..., min_length=1, description="Single key code")


class KeyCreateBulk(BaseModel):
    raw_text: str = Field(..., description="Newline-separated list of keys")


class KeyBulkImportResult(BaseModel):
    total_lines: int
    saved_count: int
    duplicates_or_skipped: int
    invalid_count: int = 0


# ---- Settings ----
class BotSettingsUpdate(BaseModel):
    ai_base_url: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None
    loading_timeout_seconds: Optional[int] = None
    circuit_breaker_limit: Optional[int] = None
    action_delay_seconds: Optional[float] = None
    watchdog_interval_seconds: Optional[int] = None
    preview_refresh_seconds: Optional[int] = None


class TestAIRequest(BaseModel):
    ai_base_url: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None
