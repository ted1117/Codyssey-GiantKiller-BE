from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.models.verification import VerificationAnswer, VerificationStatus


class VerificationRespond(BaseModel):
    """확인 요청 ID와 로그인한 자녀의 발송 여부만 입력받습니다."""

    model_config = ConfigDict(extra="forbid")

    verification_id: UUID
    response: VerificationAnswer


class VerificationRead(BaseModel):
    """가족 확인 상태·집계와 현재 사용자의 응답을 반환합니다."""

    id: UUID
    event_id: UUID
    status: VerificationStatus
    expires_at: AwareDatetime
    target_count: int = Field(ge=0)
    responded_count: int = Field(ge=0)
    my_response: VerificationAnswer | None
