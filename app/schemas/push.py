from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def validate_fid(value: str) -> str:
    """FID 원문을 보존하고 빈 값·NUL·길이 초과를 거부합니다."""
    if not value.strip():
        raise ValueError("FCM FID는 비어 있을 수 없습니다")
    if "\x00" in value:
        raise ValueError("FCM FID에는 NUL 문자를 포함할 수 없습니다")
    if len(value.encode("utf-8")) > 2048:
        raise ValueError("FCM FID는 2048바이트 이하여야 합니다")
    return value


FID = Annotated[str, Field(max_length=2048), AfterValidator(validate_fid)]


class PushRegistrationDelete(BaseModel):
    """자신의 연결을 해제할 FID만 받습니다."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    fid: FID = Field(repr=False)


class PushRegistrationUpsert(PushRegistrationDelete):
    """현재 FID와 선택적인 이전 FID를 받습니다."""

    previous_fid: FID | None = Field(default=None, repr=False)


class PushMessageType(StrEnum):
    """독립 발송 서비스가 지원하는 알림 유형입니다."""

    RISK_WARNING = "RISK_WARNING"
    CALL_SUMMARY = "CALL_SUMMARY"
    VERIFY_REQUEST = "VERIFY_REQUEST"
    VERIFY_WAITING = "VERIFY_WAITING"
    VERIFY_CONFIRMED = "VERIFY_CONFIRMED"
    VERIFY_DENIED = "VERIFY_DENIED"
    VERIFY_EXPIRED = "VERIFY_EXPIRED"


class PushSend(BaseModel):
    """내부 서비스가 전달하는 수신자·사건·메시지 유형입니다."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    event_id: UUID
    message_type: PushMessageType
    verification_id: UUID | None = None

    @model_validator(mode="after")
    def require_verification_id(self) -> Self:
        """가족 확인 알림에 확인 요청 ID를 요구합니다."""
        if self.message_type.value.startswith("VERIFY_"):
            if self.verification_id is None:
                raise ValueError("가족 확인 알림에는 verification_id가 필요합니다")
        elif self.verification_id is not None:
            raise ValueError("일반 알림에는 verification_id를 지정할 수 없습니다")
        return self


class PushSendResult(BaseModel):
    """기기 수신 여부와 구분하여 FCM 발송 시도 결과를 반환합니다."""

    success_count: int = 0
    failure_count: int = 0
    skipped_reason: str | None = None
