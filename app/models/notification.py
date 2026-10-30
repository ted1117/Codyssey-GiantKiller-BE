from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NotificationType(StrEnum):
    """가족 확인에서 생성하는 알림 요청 유형입니다."""

    VERIFY_REQUEST = "VERIFY_REQUEST"
    VERIFY_WAITING = "VERIFY_WAITING"
    VERIFY_CONFIRMED = "VERIFY_CONFIRMED"
    VERIFY_DENIED = "VERIFY_DENIED"
    VERIFY_EXPIRED = "VERIFY_EXPIRED"


class NotificationRequest(Base):
    """실제 발송과 구분하여 사용자별 알림 의도를 저장합니다."""

    __tablename__ = "notification_requests"
    __table_args__ = (
        UniqueConstraint(
            "verification_id",
            "user_id",
            "message_type",
            name="uq_notification_requests_recipient_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("security_events.id"))
    verification_id: Mapped[UUID] = mapped_column(
        ForeignKey("verification_requests.id")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    message_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
