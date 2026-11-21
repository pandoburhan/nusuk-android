import datetime
import enum
import uuid
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Enum as SQLEnum, Index,
    ForeignKey, UniqueConstraint,
)
from backend.database import Base
from backend.time_service import utc_now_naive


def new_uuid() -> str:
    return str(uuid.uuid4())


class KeyStatus(str, enum.Enum):
    """LEGACY (v1) status. Kept only for the archived column `redeem_keys.status`."""
    UNUSED = "unused"
    IN_PROGRESS = "in_progress"
    USED = "used"
    INVALID = "invalid"


class ClaimStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    USED = "used"
    INVALID = "invalid"


class KeySource(str, enum.Enum):
    MANUAL = "manual"
    BULK = "bulk"


class QuizResultStatus(str, enum.Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNKNOWN = "unknown"


class LogSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Instance(Base):
    """An Android node (emulator or physical phone). `uuid` is permanent; connection attributes are mutable."""
    __tablename__ = "instances"

    uuid = Column(String(36), primary_key=True, default=new_uuid)
    name = Column(String(64), nullable=False)
    connection_type = Column(String(16), nullable=False, default="tcp")  # 'tcp' | 'usb' | 'wireless'
    adb_serial = Column(String(128), nullable=True, index=True)          # '<ip>:<port>' or USB hardware serial
    ip = Column(String(64), nullable=True)
    port = Column(Integer, nullable=True, default=5555)
    live_preview_enabled = Column(Boolean, nullable=False, default=True)
    nusuk_installed = Column(Boolean, nullable=True)
    anchor_planted = Column(Boolean, nullable=False, default=False)
    is_legacy_primary = Column(Boolean, nullable=False, default=False)
    last_app_keys = Column(Integer, nullable=True)
    last_mode = Column(String(32), nullable=True)
    last_status = Column(String(16), nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)

    @property
    def address(self) -> str:
        if self.adb_serial:
            return self.adb_serial
        if self.ip and self.port:
            return f"{self.ip}:{self.port}"
        return self.ip or ""


class RedeemKey(Base):
    """Master inventory of codes (v2). Per-device state lives in DeviceKeyClaim."""
    __tablename__ = "redeem_keys"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(128), unique=True, nullable=False, index=True)
    source = Column(SQLEnum(KeySource), default=KeySource.MANUAL, nullable=False)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    is_blacklisted = Column(Boolean, nullable=False, default=False, index=True)
    blacklisted_by_device_uuid = Column(String(36), nullable=True)
    blacklist_reason = Column(Text, nullable=True)
    blacklisted_at = Column(DateTime, nullable=True)

    # ---- LEGACY v1 columns: preserved for zero-data-loss, never read by v2 logic ----
    legacy_status = Column("status", SQLEnum(KeyStatus), default=KeyStatus.UNUSED, nullable=False)
    legacy_device_id = Column("device_id", String(64), nullable=True)
    legacy_error_note = Column("error_note", Text, nullable=True)
    legacy_used_at = Column("used_at", DateTime, nullable=True)
    legacy_duration_ms = Column("duration_ms", Integer, nullable=True, default=None)


class DeviceKeyClaim(Base):
    __tablename__ = "device_key_claims"
    __table_args__ = (
        UniqueConstraint("key_id", "device_uuid", name="uq_claim_key_device"),
        Index("ix_claim_device_status", "device_uuid", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_id = Column(Integer, ForeignKey("redeem_keys.id", ondelete="CASCADE"), nullable=False, index=True)
    device_uuid = Column(String(36), ForeignKey("instances.uuid"), nullable=False)
    status = Column(SQLEnum(ClaimStatus), nullable=False, default=ClaimStatus.IN_PROGRESS)
    duration_ms = Column(Integer, nullable=True)
    error_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class QuizLog(Base):
    __tablename__ = "quiz_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device_id = Column(String(64), nullable=False)  # legacy textual IP:Port snapshot
    device_uuid = Column(String(36), nullable=True, index=True)
    mode = Column(String(32), nullable=False)
    door_category = Column(String(64), nullable=False)
    question_text = Column(Text, nullable=False)
    options_json = Column(Text, nullable=False)
    ai_selected_answer = Column(Text, nullable=True)
    ai_selected_index = Column(Integer, nullable=True)
    result_status = Column(SQLEnum(QuizResultStatus), default=QuizResultStatus.UNKNOWN, nullable=False, index=True)
    duration_ms = Column(Integer, default=0)
    ai_duration_ms = Column(Integer, nullable=True)  # NULL = not measured (legacy v1 rows)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False, index=True)


class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    severity = Column(SQLEnum(LogSeverity), default=LogSeverity.INFO, nullable=False, index=True)
    category = Column(String(64), nullable=False, index=True)
    message = Column(Text, nullable=False)
    last_screen_state = Column(String(64), nullable=True)
    device_uuid = Column(String(36), nullable=True, index=True)  # NULL = GLOBAL
    created_at = Column(DateTime, default=utc_now_naive, nullable=False, index=True)


class BotSetting(Base):
    __tablename__ = "bot_settings"

    setting_key = Column(String(64), primary_key=True)
    setting_value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)
