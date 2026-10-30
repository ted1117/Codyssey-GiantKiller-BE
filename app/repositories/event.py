from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import SecurityEvent


class SecurityEventRepository:
    """사건을 조회·저장하며 commit은 호출자가 담당합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사용할 세션을 저장합니다."""
        self.session = session

    async def create(
        self,
        family_id: UUID,
        parent_member_id: UUID,
        event_type: str,
        classification: str,
        occurred_at: datetime,
        *,
        risk_level: str | None = None,
        summary: str | None = None,
    ) -> SecurityEvent:
        """사건을 저장하며 가족·역할과 분류 정책은 호출자가 검증합니다."""
        event = SecurityEvent(
            family_id=family_id,
            parent_member_id=parent_member_id,
            event_type=event_type,
            classification=classification,
            occurred_at=occurred_at,
            risk_level=risk_level,
            summary=summary,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def get(
        self, event_id: UUID, *, for_update: bool = False
    ) -> SecurityEvent | None:
        """사건을 조회하고 필요하면 확인 요청 생성이 겹치지 않게 잠급니다."""
        statement = select(SecurityEvent).where(SecurityEvent.id == event_id)
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return await self.session.scalar(statement)
