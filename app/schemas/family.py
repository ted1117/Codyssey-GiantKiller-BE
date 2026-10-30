from uuid import UUID

from pydantic import (
    AliasChoices,
    AliasPath,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
)

from app.models.family import FamilyRole


class FamilyRead(BaseModel):
    """가족의 식별자와 생성 시각을 반환합니다."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: AwareDatetime


class FamilyMemberRead(BaseModel):
    """계정 이름과 가족별 역할·대표 여부를 반환합니다."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str = Field(validation_alias=AliasChoices("name", AliasPath("user", "name")))
    role: FamilyRole
    is_representative: bool
    joined_at: AwareDatetime


class FamilyDetail(FamilyRead):
    """가족 정보와 멤버 목록을 함께 반환합니다."""

    members: list[FamilyMemberRead]
