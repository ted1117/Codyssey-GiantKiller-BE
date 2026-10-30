from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.family import FamilyRole, family_role


class FamilyInvitation(Base):
    """초대 토큰 해시와 수락·취소 이력을 저장합니다."""

    __tablename__ = "family_invitations"
    __table_args__ = (
        CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by_user_id IS NULL)",
            name="ck_family_invitations_acceptance_pair",
        ),
        CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name="ck_family_invitations_single_outcome",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[UUID] = mapped_column(ForeignKey("families.id"), index=True)
    created_by_member_id: Mapped[UUID] = mapped_column(ForeignKey("family_members.id"))
    role: Mapped[FamilyRole] = mapped_column(family_role)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
