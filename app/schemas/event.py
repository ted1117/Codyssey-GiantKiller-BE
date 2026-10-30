from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool


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
