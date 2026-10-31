from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_instruction import CallInstruction
from app.schemas.event import CallInstructionInput


class CallInstructionRepository:
    """통화에서 추출한 지시를 저장합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사건과 동일한 세션을 저장합니다."""
        self.session = session

    async def create_many(
        self, event_id: UUID, instructions: list[CallInstructionInput]
    ) -> None:
        """지시를 함께 저장하며 commit은 호출자가 담당합니다."""
        self.session.add_all(
            [
                CallInstruction(event_id=event_id, **item.model_dump())
                for item in instructions
            ]
        )
        await self.session.flush()

    async def list_by_event(self, event_id: UUID) -> list[CallInstruction]:
        """사건의 지시를 유형·ID 순서로 조회합니다."""
        result = await self.session.scalars(
            select(CallInstruction)
            .where(CallInstruction.event_id == event_id)
            .order_by(CallInstruction.instruction_type, CallInstruction.id)
        )
        return list(result)
