from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
)

from app.schemas.analysis import nonblank
from app.schemas.event import TextValue


class AppInstallCreate(BaseModel):
    """부모 기기가 보고하는 앱 설치 정보입니다."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    family_id: UUID
    package_name: Annotated[
        TextValue, Field(min_length=1, max_length=255), AfterValidator(nonblank)
    ]
    app_label: Annotated[
        TextValue, Field(min_length=1, max_length=200), AfterValidator(nonblank)
    ]
    is_unknown_source: StrictBool
    occurred_at: AwareDatetime


class AppInstallRead(BaseModel):
    """위험 앱 판정 결과와 최근 의심 통화 여부를 반환합니다."""

    matched: bool
    event_id: UUID | None = None
    related_call: bool = False
