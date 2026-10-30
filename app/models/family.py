from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class FamilyRole(StrEnum):
    """가족별 부모·자녀 역할을 정의합니다."""

    PARENT = "PARENT"
    CHILD = "CHILD"


family_role = Enum(FamilyRole, name="family_role")


class Family(Base):
    """가족을 저장합니다."""

    __tablename__ = "families"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FamilyMember(Base):
    """사용자의 가족별 역할과 대표 여부를 저장합니다."""

    __tablename__ = "family_members"
    __table_args__ = (
        UniqueConstraint("family_id", "user_id", name="uq_family_members_family_user"),
        CheckConstraint(
            "NOT is_representative OR role = 'CHILD'",
            name="ck_family_members_representative_child",
        ),
        Index(
            "uq_family_members_representative",
            "family_id",
            unique=True,
            postgresql_where=text("is_representative"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[UUID] = mapped_column(ForeignKey("families.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[FamilyRole] = mapped_column(family_role)
    is_representative: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    user: Mapped[User] = relationship(lazy="raise")
