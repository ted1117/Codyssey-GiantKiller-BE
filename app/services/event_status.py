import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.event import EventStatus
from app.models.family import FamilyRole
from app.repositories.event import SecurityEventRepository
from app.schemas.event import EventRead, EventStatusUpdate
from app.services.family import FamilyService
from app.services.transaction import transaction

logger = logging.getLogger(__name__)


class EventStatusService:
    """가족 자녀의 명시적 선택으로 사건 대응 상태를 변경합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사건 저장소와 가족 권한 서비스에 요청 세션을 공유합니다."""
        self.session = session
        self.events = SecurityEventRepository(session)
        self.families = FamilyService(session)

    async def update(
        self, event_id: UUID, user_id: UUID, body: EventStatusUpdate
    ) -> EventRead:
        """공개 사건을 잠그고 권한·전이를 검사한 뒤 변경 결과를 반환합니다."""
        target = EventStatus(body.status)
        async with transaction(self.session):
            event = await self.events.get_visible(event_id, for_update=True)
            if event is None:
                raise ServiceError(404, "EVENT_NOT_FOUND")
            member = await self.families.require_member(event.family_id, user_id)
            if member.role != FamilyRole.CHILD:
                raise ServiceError(403, "CHILD_REQUIRED")
            previous = event.status
            if previous != target:
                allowed = (
                    previous == EventStatus.OPEN
                    or previous == EventStatus.ACKNOWLEDGED
                    and target == EventStatus.RESOLVED
                )
                if not allowed:
                    raise ServiceError(409, "EVENT_STATUS_CONFLICT")
                await self.events.update_status(event, target)
            result = EventRead.model_validate(event)
        logger.info(
            "event_status_updated event_id=%s user_id=%s previous=%s status=%s",
            event_id,
            user_id,
            previous,
            target,
        )
        return result
