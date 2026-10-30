from uuid import UUID

from pydantic import AnyUrl, AwareDatetime, BaseModel, ConfigDict

from app.models.family import FamilyRole


class InvitationCreate(BaseModel):
    """초대할 멤버의 역할만 입력받습니다."""

    model_config = ConfigDict(extra="forbid")

    role: FamilyRole


class InvitationCreated(BaseModel):
    """생성한 초대의 공유 링크와 만료 시각을 반환합니다."""

    id: UUID
    family_id: UUID
    role: FamilyRole
    invite_url: AnyUrl
    expires_at: AwareDatetime


class InvitationPreview(BaseModel):
    """수락 전에 확인할 가족·대표·참여 역할을 반환합니다."""

    family_id: UUID
    representative_name: str
    role: FamilyRole
    expires_at: AwareDatetime
