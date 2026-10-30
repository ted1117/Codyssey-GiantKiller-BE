from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VerificationStatus(StrEnum):
    """확인 요청의 대기·발송 확인·발송 부인·만료 상태입니다."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


class VerificationAnswer(StrEnum):
    """자녀가 선택하는 발송 여부입니다."""

    SENT = "SENT"
    NOT_SENT = "NOT_SENT"


class VerificationRequest(Base):
    """사건별 가족 확인 상태와 응답 기한을 저장합니다."""

    __tablename__ = "verification_requests"
    __table_args__ = (
        CheckConstraint(
            "expires_at >= created_at", name="ck_verification_requests_expiry"
        ),
        CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= created_at",
            name="ck_verification_requests_finalized_time",
        ),
        CheckConstraint(
            "(status = 'PENDING') = (finalized_at IS NULL)",
            name="ck_verification_requests_finalized_state",
        ),
        Index("ix_verification_requests_status_expiry", "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("security_events.id"), unique=True
    )
    family_id: Mapped[UUID] = mapped_column(ForeignKey("families.id"))
    parent_member_id: Mapped[UUID] = mapped_column(ForeignKey("family_members.id"))
    status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationResponse(Base):
    """요청 대상 자녀와 미응답을 포함한 개인별 응답을 저장합니다."""

    __tablename__ = "verification_responses"
    __table_args__ = (
        UniqueConstraint(
            "verification_id", "member_id", name="uq_verification_responses_member"
        ),
        CheckConstraint(
            "(response IS NULL) = (responded_at IS NULL)",
            name="ck_verification_responses_answer_time",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    verification_id: Mapped[UUID] = mapped_column(
        ForeignKey("verification_requests.id")
    )
    member_id: Mapped[UUID] = mapped_column(ForeignKey("family_members.id"))
    response: Mapped[VerificationAnswer | None] = mapped_column(
        Enum(VerificationAnswer, name="verification_answer")
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
