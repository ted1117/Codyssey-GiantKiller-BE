from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from app.schemas.event import CallAnalysisResult, Code, TextValue


def nonblank(value: str) -> str:
    """공백뿐인 문자를 거부합니다."""
    if not value.strip():
        raise ValueError("문자는 비어 있을 수 없습니다")
    return value


class AlertCheck(BaseModel):
    """인증된 부모가 분석할 문자를 전달합니다."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    family_id: UUID
    occurred_at: AwareDatetime
    text: Annotated[TextValue, AfterValidator(nonblank)]


class AlertAnalysisResult(BaseModel):
    """AI 응답을 검증한 문자 분석 결과입니다."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    classification: Code
    requires_attention: StrictBool
    risk_level: Annotated[TextValue, Field(max_length=32)] | None = None
    summary: TextValue | None = None

    @model_validator(mode="after")
    def check_attention(self) -> Self:
        """사칭 분류와 주의 불필요 판정의 모순을 거부합니다."""
        if self.classification == "CHILD_IMPERSONATION" and not self.requires_attention:
            raise ValueError("사칭 분류는 주의가 필요합니다")
        return self


class AlertCheckRead(BaseModel):
    """문자 분석 결과와 생성된 확인 요청을 반환합니다."""

    result: AlertAnalysisResult
    event_id: UUID | None = None
    verification_id: UUID | None = None


class CallAnalyzeRead(BaseModel):
    """통화 사건의 분석 결과 또는 실패를 반환합니다."""

    event_id: UUID
    analysis_status: Literal["COMPLETED", "FAILED"]
    status: Literal["OPEN", "NOT_REQUIRED"]
    result: CallAnalysisResult | None = None
    error_code: Literal["AI_REQUEST_FAILED", "INVALID_ANALYSIS_RESULT"] | None = None
