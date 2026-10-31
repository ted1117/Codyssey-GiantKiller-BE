from datetime import UTC, datetime
from uuid import UUID

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import AIClient
from app.core.exceptions import ServiceError
from app.models.family import FamilyRole
from app.repositories.event import SecurityEventRepository
from app.schemas.analysis import (
    AlertAnalysisResult,
    AlertCheck,
    AlertCheckRead,
    CallAnalyzeRead,
)
from app.schemas.event import CallAnalysisResult
from app.services.event import EventService
from app.services.family import FamilyService
from app.services.push import PushService
from app.services.transaction import transaction
from app.services.verification import VerificationService


class AnalysisService:
    """AI 호출과 기존 사건·가족 확인 서비스를 연결합니다."""

    def __init__(self, session: AsyncSession, ai: AIClient, push: PushService) -> None:
        """요청 세션과 분석·알림 의존성을 저장합니다."""
        self.session = session
        self.ai = ai
        self.push = push
        self.events = EventService(session, push)

    async def _parent(self, family_id: UUID, user_id: UUID) -> UUID:
        async with transaction(self.session):
            member = await FamilyService(self.session).require_member(
                family_id, user_id
            )
            if member.role != FamilyRole.PARENT:
                raise ServiceError(403, "PARENT_REQUIRED")
            member_id = member.id
        if not self.ai.configured:
            raise ServiceError(503, "AI_NOT_CONFIGURED")
        return member_id

    async def check_alert(self, user_id: UUID, body: AlertCheck) -> AlertCheckRead:
        """문자를 분석하고 사칭 사건과 가족 확인을 함께 저장합니다."""
        member_id = await self._parent(body.family_id, user_id)
        try:
            result = AlertAnalysisResult.model_validate(
                await self.ai.analyze_alert(body.text)
            )
        except ValidationError:
            raise ServiceError(502, "INVALID_ANALYSIS_RESULT") from None
        except OSError, TimeoutError:
            raise ServiceError(502, "AI_REQUEST_FAILED") from None
        event_id = verification_id = None
        if result.classification == "CHILD_IMPERSONATION":
            try:
                event = await SecurityEventRepository(self.session).create(
                    body.family_id,
                    member_id,
                    "ALERT",
                    result.classification,
                    body.occurred_at.astimezone(UTC),
                    risk_level=result.risk_level,
                    summary=result.summary,
                )
                request = await VerificationService(self.session, self.push).create(
                    event.id
                )
                event_id, verification_id = event.id, request.id
            except BaseException:
                await self.session.rollback()
                raise
        return AlertCheckRead(
            result=result, event_id=event_id, verification_id=verification_id
        )

    async def analyze_call(
        self, user_id: UUID, family_id: UUID, occurred_at: datetime, file: UploadFile
    ) -> CallAnalyzeRead:
        """통화를 선저장하고 같은 사건에 분석 결과 또는 실패를 남깁니다."""
        try:
            if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
                raise ServiceError(422, "TIMEZONE_REQUIRED")
            size = 0
            while chunk := await file.read(65536):
                size += len(chunk)
            await file.seek(0)
            if not size:
                raise ServiceError(422, "EMPTY_AUDIO")
            await self._parent(family_id, user_id)
            event_id = await self.events.create_call(family_id, user_id, occurred_at)
            await self.events.start_analysis(event_id)
            error_code = None
            try:
                result = CallAnalysisResult.model_validate(
                    await self.ai.analyze_call(file)
                )
            except ValidationError:
                error_code = "INVALID_ANALYSIS_RESULT"
            except OSError, TimeoutError:
                error_code = "AI_REQUEST_FAILED"
            if error_code:
                await self.events.fail_analysis(event_id, error_code)
                return CallAnalyzeRead(
                    event_id=event_id,
                    analysis_status="FAILED",
                    status="OPEN",
                    error_code=error_code,
                )
            await self.events.complete_analysis(event_id, result)
            return CallAnalyzeRead(
                event_id=event_id,
                analysis_status="COMPLETED",
                status="OPEN" if result.requires_attention else "NOT_REQUIRED",
                result=result,
            )
        finally:
            await file.close()
