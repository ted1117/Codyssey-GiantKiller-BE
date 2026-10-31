from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.family import FamilyRole
from app.repositories.call_instruction import CallInstructionRepository
from app.repositories.event import SecurityEventRepository
from app.schemas.event import EventDetailRead, EventInstructionRead, EventRead
from app.services.family import FamilyService


class EventQueryService:
    """가족의 자녀에게 공개 대상 사건만 제공합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """조회 저장소와 가족 권한 서비스를 구성합니다."""
        self.events = SecurityEventRepository(session)
        self.instructions = CallInstructionRepository(session)
        self.families = FamilyService(session)

    async def _require_child(self, family_id: UUID, user_id: UUID) -> None:
        member = await self.families.require_member(family_id, user_id)
        if member.role != FamilyRole.CHILD:
            raise ServiceError(403, "CHILD_REQUIRED")

    async def list_by_family(
        self, family_id: UUID, user_id: UUID, limit: int, offset: int
    ) -> list[EventRead]:
        """해당 가족 자녀에게 사건 목록을 반환합니다."""
        await self._require_child(family_id, user_id)
        return [
            EventRead.model_validate(event)
            for event in await self.events.list_visible(family_id, limit, offset)
        ]

    async def get_detail(self, event_id: UUID, user_id: UUID) -> EventDetailRead:
        """해당 가족 자녀에게 사건 상세를 반환하며 숨긴 사건은 404입니다."""
        event = await self.events.get_visible(event_id)
        if event is None:
            raise ServiceError(404, "EVENT_NOT_FOUND")
        await self._require_child(event.family_id, user_id)
        instructions = await self.instructions.list_by_event(event.id)
        return EventDetailRead(
            **EventRead.model_validate(event).model_dump(),
            instructions=[
                EventInstructionRead.model_validate(item) for item in instructions
            ],
        )
