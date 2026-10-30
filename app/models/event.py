from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AnalysisStatus(StrEnum):
    """통화 분석의 처리 상태입니다."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EventStatus(StrEnum):
    """가족의 사건 대응 상태입니다."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    NOT_REQUIRED = "NOT_REQUIRED"


class SecurityEvent(Base):
    """통화 분석과 가족 확인에 사용하는 사건을 저장합니다."""

    __tablename__ = "security_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[UUID] = mapped_column(ForeignKey("families.id"))
    parent_member_id: Mapped[UUID] = mapped_column(ForeignKey("family_members.id"))
    event_type: Mapped[str] = mapped_column(String(32))
    classification: Mapped[str | None] = mapped_column(String(64))
    requires_attention: Mapped[bool | None] = mapped_column()
    analysis_status: Mapped[str | None] = mapped_column(String(32))
    analysis_error_code: Mapped[str | None] = mapped_column(String(64))
    risk_level: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), default="OPEN", server_default="OPEN"
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
