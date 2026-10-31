import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.event import AnalysisStatus, EventStatus, SecurityEvent
from app.models.family import FamilyRole
from app.repositories.call_instruction import CallInstructionRepository
from app.repositories.event import SecurityEventRepository
from app.repositories.family import FamilyMemberRepository
from app.schemas.analysis import AlertAnalysisResult
from app.schemas.event import CallAnalysisResult
from app.schemas.push import PushMessageType, PushSend
from app.services.family import FamilyService
from app.services.push import PushService
from app.services.transaction import transaction

logger = logging.getLogger(__name__)
_FAILURE_CODES = {"AI_REQUEST_FAILED", "INVALID_ANALYSIS_RESULT"}


class EventService:
    """통화 분석 상태와 결과를 저장하고 commit 후 자녀에게 알립니다."""

    def __init__(self, session: AsyncSession, push: PushService) -> None:
        """저장 세션과 독립 발송 서비스를 주입받습니다."""
        self.session = session
        self.push = push
        self.events = SecurityEventRepository(session)
        self.instructions = CallInstructionRepository(session)
        self.members = FamilyMemberRepository(session)

    async def create_call(
        self, family_id: UUID, parent_user_id: UUID, occurred_at: datetime
    ) -> UUID:
        """인증된 부모의 가족 소속을 검사하고 분석 전 사건을 확정합니다."""
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("통화 시각에는 타임존이 필요합니다")
        async with transaction(self.session):
            member = await FamilyService(self.session).require_member(
                family_id, parent_user_id
            )
            if member.role != FamilyRole.PARENT:
                raise ServiceError(403, "PARENT_REQUIRED")
            event = await self.events.create_call(
                family_id, member.id, occurred_at.astimezone(UTC)
            )
            event_id = event.id
        logger.info("call_event_created event_id=%s", event_id)
        return event_id

    async def _require_state(
        self, event_id: UUID, expected: AnalysisStatus
    ) -> SecurityEvent:
        event = await self.events.get(event_id, for_update=True)
        if event is None:
            raise ServiceError(404, "EVENT_NOT_FOUND")
        if event.event_type != "CALL":
            raise ServiceError(409, "EVENT_NOT_CALL")
        if event.analysis_status != expected:
            raise ServiceError(409, "EVENT_ANALYSIS_STATE_CONFLICT")
        return event

    async def start_analysis(self, event_id: UUID) -> None:
        """대기 중인 통화를 분석 중으로 변경합니다."""
        async with transaction(self.session):
            event = await self._require_state(event_id, AnalysisStatus.PENDING)
            await self.events.start_analysis(event)

    async def complete_analysis(
        self, event_id: UUID, result: CallAnalysisResult
    ) -> None:
        """분석 결과·지시 저장을 확정한 뒤 위험·의심 통화만 알립니다."""
        targets = []
        async with transaction(self.session):
            event = await self._require_state(event_id, AnalysisStatus.PROCESSING)
            await self.events.complete_analysis(
                event,
                result,
                EventStatus.OPEN
                if result.requires_attention
                else EventStatus.NOT_REQUIRED,
            )
            await self.instructions.create_many(event_id, result.instructions)
            if event.requires_attention:
                targets = [
                    m.user_id
                    for m in await self.members.list_by_family(event.family_id)
                    if m.role == FamilyRole.CHILD
                ]
        logger.info("call_analysis_completed event_id=%s", event_id)
        for user_id in targets:
            try:
                await self.push.send(
                    PushSend(
                        user_id=user_id,
                        event_id=event_id,
                        message_type=PushMessageType.CALL_SUMMARY,
                    )
                )
            except Exception as error:
                logger.error(
                    "call_push_failed event_id=%s user_id=%s code=%s",
                    event_id,
                    user_id,
                    type(error).__name__,
                )

    async def create_alert(
        self,
        family_id: UUID,
        parent_member_id: UUID,
        occurred_at: datetime,
        result: AlertAnalysisResult,
    ) -> UUID:
        """주의가 필요한 비사칭 문자를 사건으로 확정한 뒤 자녀에게 알립니다."""
        async with transaction(self.session):
            event = await self.events.create(
                family_id,
                parent_member_id,
                "ALERT",
                result.classification,
                occurred_at.astimezone(UTC),
                risk_level=result.risk_level,
                summary=result.summary,
                requires_attention=True,
                analysis_status=AnalysisStatus.COMPLETED,
            )
            event_id = event.id
            targets = [
                m.user_id
                for m in await self.members.list_by_family(family_id)
                if m.role == FamilyRole.CHILD
            ]
        logger.info("alert_event_created event_id=%s", event_id)
        for user_id in targets:
            try:
                await self.push.send(
                    PushSend(
                        user_id=user_id,
                        event_id=event_id,
                        message_type=PushMessageType.RISK_WARNING,
                    )
                )
            except Exception as error:
                logger.error(
                    "alert_push_failed event_id=%s user_id=%s code=%s",
                    event_id,
                    user_id,
                    type(error).__name__,
                )
        return event_id

    async def fail_analysis(self, event_id: UUID, error_code: str) -> None:
        """AI 호출·검증을 담당하는 호출부가 분석 실패를 기록합니다."""
        if error_code not in _FAILURE_CODES:
            raise ValueError("정의된 분석 실패 코드가 필요합니다")
        async with transaction(self.session):
            event = await self._require_state(event_id, AnalysisStatus.PROCESSING)
            await self.events.fail_analysis(event, error_code)
        logger.warning("call_analysis_failed event_id=%s code=%s", event_id, error_code)
