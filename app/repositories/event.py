from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import AnalysisStatus, EventStatus, SecurityEvent
from app.schemas.event import CallAnalysisResult


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

    async def create_call(
        self, family_id: UUID, parent_member_id: UUID, occurred_at: datetime
    ) -> SecurityEvent:
        """분석 전 통화 사건을 저장합니다."""
        event = SecurityEvent(
            family_id=family_id,
            parent_member_id=parent_member_id,
            event_type="CALL",
            occurred_at=occurred_at,
            analysis_status=AnalysisStatus.PENDING,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def start_analysis(self, event: SecurityEvent) -> None:
        """분석 시작을 저장합니다."""
        event.analysis_status = AnalysisStatus.PROCESSING
        await self.session.flush()

    async def complete_analysis(
        self, event: SecurityEvent, result: CallAnalysisResult, status: EventStatus
    ) -> None:
        """검증된 결과와 판정값을 저장합니다."""
        event.classification = result.classification
        event.requires_attention = result.requires_attention
        event.risk_level = result.risk_level
        event.summary = result.summary
        event.analysis_status = AnalysisStatus.COMPLETED
        event.analysis_error_code = None
        event.status = status
        await self.session.flush()

    async def fail_analysis(self, event: SecurityEvent, error_code: str) -> None:
        """고정 분석 실패 코드를 저장합니다."""
        event.analysis_status = AnalysisStatus.FAILED
        event.analysis_error_code = error_code
        await self.session.flush()
