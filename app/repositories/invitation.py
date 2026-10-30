from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.family import FamilyRole
from app.models.invitation import FamilyInvitation


class FamilyInvitationRepository:
    """초대 데이터를 처리하며 유효성 판단과 commit은 호출자가 담당합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사용할 세션을 저장합니다."""
        self.session = session

    async def create(
        self,
        family_id: UUID,
        created_by_member_id: UUID,
        role: FamilyRole,
        token_hash: str,
        expires_at: datetime,
    ) -> FamilyInvitation:
        """토큰 해시와 만료 시각을 받아 초대를 저장합니다."""
        invitation = FamilyInvitation(
            family_id=family_id,
            created_by_member_id=created_by_member_id,
            role=role,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def get(
        self, family_id: UUID, invitation_id: UUID, *, for_update: bool = False
    ) -> FamilyInvitation | None:
        """해당 가족의 초대를 조회하고 필요하면 행을 잠급니다."""
        statement = select(FamilyInvitation).where(
            FamilyInvitation.id == invitation_id,
            FamilyInvitation.family_id == family_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return await self.session.scalar(statement)

    async def get_by_token_hash(
        self, token_hash: str, *, for_update: bool = False
    ) -> FamilyInvitation | None:
        """토큰 해시로 초대를 조회하고 필요하면 행을 잠급니다."""
        statement = select(FamilyInvitation).where(
            FamilyInvitation.token_hash == token_hash
        )
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return await self.session.scalar(statement)

    async def mark_accepted(
        self, invitation: FamilyInvitation, user_id: UUID, accepted_at: datetime
    ) -> None:
        """호출자가 검증한 초대에 수락자와 수락 시각을 기록합니다."""
        invitation.accepted_by_user_id = user_id
        invitation.accepted_at = accepted_at
        await self.session.flush()

    async def mark_revoked(
        self, invitation: FamilyInvitation, revoked_at: datetime
    ) -> None:
        """호출자가 검증한 초대에 취소 시각을 기록합니다."""
        invitation.revoked_at = revoked_at
        await self.session.flush()
