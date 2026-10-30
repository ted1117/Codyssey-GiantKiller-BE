from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_registration import PushRegistration
from app.models.user import User


class PushRegistrationRepository:
    """FCM FID 조회·갱신·삭제를 수행하고 commit은 호출자에게 맡깁니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사용할 DB 세션을 저장합니다."""
        self.session = session

    async def upsert(self, user_id: UUID, fid: str) -> None:
        """FID을 중복 생성하지 않고 현재 사용자로 연결합니다."""
        statement = insert(PushRegistration).values(user_id=user_id, fid=fid)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[PushRegistration.fid],
                set_={"user_id": user_id, "updated_at": func.clock_timestamp()},
            )
        )

    async def delete_by_user(self, user_id: UUID, fid: str) -> None:
        """해당 사용자와 연결된 FID만 삭제합니다."""
        await self.session.execute(
            delete(PushRegistration).where(
                PushRegistration.user_id == user_id, PushRegistration.fid == fid
            )
        )

    async def is_active_user(self, user_id: UUID) -> bool:
        """존재하는 활성 사용자인지 조회합니다."""
        return (
            await self.session.scalar(select(User.is_active).where(User.id == user_id))
        ) is True

    async def list_by_user(self, user_id: UUID) -> list[PushRegistration]:
        """사용자의 모든 FID을 일정한 순서로 조회합니다."""
        return list(
            await self.session.scalars(
                select(PushRegistration)
                .where(PushRegistration.user_id == user_id)
                .order_by(PushRegistration.id)
            )
        )

    async def delete_if_unchanged(
        self, registration_id: UUID, user_id: UUID, updated_at: datetime
    ) -> None:
        """발송 후 재등록·계정 전환되지 않은 무효 FID만 삭제합니다."""
        await self.session.execute(
            delete(PushRegistration).where(
                PushRegistration.id == registration_id,
                PushRegistration.user_id == user_id,
                PushRegistration.updated_at == updated_at,
            )
        )
