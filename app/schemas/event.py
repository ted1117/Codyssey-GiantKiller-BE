from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)


def validate_text(value: str) -> str:
    """DB에서 허용하지 않는 NUL을 거부합니다."""
    if "\x00" in value:
        raise ValueError("NUL은 사용할 수 없습니다")
    return value


def validate_code(value: str) -> str:
    """빈 분류나 지시 유형을 거부합니다."""
    if not value.strip():
        raise ValueError("빈 코드는 사용할 수 없습니다")
    return value


TextValue = Annotated[str, AfterValidator(validate_text)]
Code = Annotated[TextValue, Field(max_length=64), AfterValidator(validate_code)]


class EventRead(BaseModel):
    """자녀에게 사건의 최소 요약과 상태·발생 시각을 반환합니다."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    summary: str | None
    analysis_status: str | None
    status: str
    occurred_at: datetime


class EventStatusUpdate(BaseModel):
    """자녀가 선택한 확인·해결 상태만 입력받습니다."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ACKNOWLEDGED", "RESOLVED"]


class CallInstructionInput(BaseModel):
    """AI 어댑터가 검증한 통화 지시입니다."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    instruction_type: Code
    description: TextValue | None = None


class CallAnalysisResult(BaseModel):
    """AI 응답과 구분되는 내부 분석 결과 계약입니다."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    classification: Code
    requires_attention: StrictBool
    risk_level: Annotated[TextValue, Field(max_length=32)] | None = None
    summary: TextValue | None = None
    instructions: list[CallInstructionInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_attention(self) -> Self:
        """사칭 분류와 주의 불필요 판정의 모순을 거부합니다."""
        if self.classification == "CHILD_IMPERSONATION" and not self.requires_attention:
            raise ValueError("사칭 분류는 주의가 필요합니다")
        return self
