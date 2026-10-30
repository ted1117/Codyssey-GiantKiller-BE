from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.push_registration import PushRegistrationRepository
from app.schemas.push import PushRegistrationUpsert
from app.services.transaction import transaction


class PushRegistrationService:
    """인증된 사용자의 FID 등록과 연결 해제를 처리합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """FID 저장소에 요청 세션을 연결합니다."""
        self.session = session
        self.fids = PushRegistrationRepository(session)

    async def register(self, user_id: UUID, body: PushRegistrationUpsert) -> None:
        """새 FID 등록과 자신의 이전 FID 삭제를 함께 확정합니다."""
        async with transaction(self.session):
            await self.fids.upsert(user_id, body.fid)
            if body.previous_fid is not None and body.previous_fid != body.fid:
                await self.fids.delete_by_user(user_id, body.previous_fid)

    async def delete(self, user_id: UUID, fid: str) -> None:
        """현재 사용자의 FID만 멱등적으로 연결 해제합니다."""
        async with transaction(self.session):
            await self.fids.delete_by_user(user_id, fid)
