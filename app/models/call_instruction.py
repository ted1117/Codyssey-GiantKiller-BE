from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CallInstruction(Base):
    """통화 사건에서 추출한 구조화된 지시를 저장합니다."""

    __tablename__ = "call_instructions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("security_events.id"), index=True)
    instruction_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
