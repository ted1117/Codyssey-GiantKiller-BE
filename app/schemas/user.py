from typing import Annotated
from uuid import UUID

from fastapi_users import schemas
from pydantic import ConfigDict, Field, StringConstraints


class UserCreate(schemas.BaseUserCreate):
    """이메일·비밀번호·이름으로 가입 정보를 검증합니다."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, repr=False)
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]


class UserRead(schemas.BaseUser[UUID]):
    """비밀번호 해시를 제외한 계정 정보를 반환합니다."""

    name: str
