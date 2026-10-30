import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import AnyUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.family import FamilyRole
from app.models.invitation import FamilyInvitation
from app.repositories.family import FamilyMemberRepository, FamilyRepository
from app.repositories.invitation import FamilyInvitationRepository
from app.schemas.family import FamilyMemberRead
from app.schemas.invitation import InvitationCreated, InvitationPreview
from app.services.family import FamilyService
from app.services.transaction import transaction


class InvitationService:
    """대표의 초대 관리와 한 번만 가능한 가족 참여를 처리합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """초대·가족·멤버 작업에 동일한 세션을 사용합니다."""
        self.session = session
        self.invitations = FamilyInvitationRepository(session)
        self.families = FamilyRepository(session)
        self.members = FamilyMemberRepository(session)
        self.family_service = FamilyService(session)

    def _require_available(
        self, invitation: FamilyInvitation | None
    ) -> FamilyInvitation:
        if invitation is None:
            raise ServiceError(404, "INVITATION_NOT_FOUND")
        if invitation.accepted_at is not None:
            raise ServiceError(409, "INVITATION_USED")
        if invitation.revoked_at is not None:
            raise ServiceError(410, "INVITATION_REVOKED")
        if invitation.expires_at <= datetime.now(UTC):
            raise ServiceError(410, "INVITATION_EXPIRED")
        return invitation

    async def _check_parent_limit(self, family_id: UUID, role: FamilyRole) -> None:
        if (
            role == FamilyRole.PARENT
            and await self.members.count_by_role(family_id, FamilyRole.PARENT) >= 2
        ):
            raise ServiceError(409, "PARENT_LIMIT_REACHED")

    async def create(
        self, family_id: UUID, user_id: UUID, role: FamilyRole, link_base_url: str
    ) -> InvitationCreated:
        """대표가 지정한 역할로 24시간 유효한 일회용 링크를 생성합니다."""
        async with transaction(self.session):
            representative = await self.family_service.require_member(
                family_id, user_id, representative=True
            )
            await self.families.get(family_id, for_update=True)
            await self._check_parent_limit(family_id, role)
            token = secrets.token_urlsafe(32)
            invitation = await self.invitations.create(
                family_id,
                representative.id,
                role,
                hashlib.sha256(token.encode()).hexdigest(),
                datetime.now(UTC) + timedelta(hours=24),
            )
            result = InvitationCreated(
                id=invitation.id,
                family_id=family_id,
                role=role,
                invite_url=AnyUrl(f"{link_base_url.rstrip('/')}/{token}"),
                expires_at=invitation.expires_at,
            )
        return result

    async def preview(self, token: str) -> InvitationPreview:
        """초대 링크로 가족과 대표 이름·참여 역할을 확인합니다."""
        invitation = self._require_available(
            await self.invitations.get_by_token_hash(
                hashlib.sha256(token.encode()).hexdigest()
            )
        )
        representative = await self.members.get_representative(invitation.family_id)
        return InvitationPreview(
            family_id=invitation.family_id,
            representative_name=representative.user.name,
            role=invitation.role,
            expires_at=invitation.expires_at,
        )

    async def accept(self, token: str, user_id: UUID) -> FamilyMemberRead:
        """가족·초대를 잠근 뒤 멤버 등록과 초대 사용을 함께 확정합니다."""
        async with transaction(self.session):
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            invitation = await self.invitations.get_by_token_hash(token_hash)
            if invitation is None:
                raise ServiceError(404, "INVITATION_NOT_FOUND")
            # 취소와 수락 모두 가족 → 초대 순서로 잠가 교착을 피한다.
            await self.families.get(invitation.family_id, for_update=True)
            invitation = self._require_available(
                await self.invitations.get_by_token_hash(token_hash, for_update=True)
            )
            if await self.members.get(invitation.family_id, user_id) is not None:
                raise ServiceError(409, "ALREADY_FAMILY_MEMBER")
            await self._check_parent_limit(invitation.family_id, invitation.role)
            await self.members.create(invitation.family_id, user_id, invitation.role)
            await self.invitations.mark_accepted(invitation, user_id, datetime.now(UTC))
            member = await self.members.get(invitation.family_id, user_id)
            result = FamilyMemberRead.model_validate(member)
        return result

    async def revoke(self, family_id: UUID, invitation_id: UUID, user_id: UUID) -> None:
        """대표가 미사용 초대를 취소하며, 취소된 초대의 재취소는 허용합니다."""
        async with transaction(self.session):
            await self.family_service.require_member(
                family_id, user_id, representative=True
            )
            await self.families.get(family_id, for_update=True)
            invitation = await self.invitations.get(
                family_id, invitation_id, for_update=True
            )
            if invitation is None:
                raise ServiceError(404, "INVITATION_NOT_FOUND")
            if invitation.revoked_at is None:
                invitation = self._require_available(invitation)
                await self.invitations.mark_revoked(invitation, datetime.now(UTC))
