import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FamilyRole, User
from app.repositories.family import FamilyMemberRepository, FamilyRepository
from app.schemas import (
    FamilyDetail,
    FamilyMemberRead,
    FamilyRead,
    InvitationCreate,
    InvitationCreated,
    InvitationPreview,
    UserCreate,
    UserRead,
)


def test_registration_normalizes_name_and_uses_safe_creation() -> None:
    """이름을 정리하고 기본 가입용 dict에서 권한 조작 필드를 제외합니다."""
    request = UserCreate(
        email="child@example.com",
        password="unchanged password ",
        name="  자녀  ",
        is_superuser=True,
        is_active=False,
        is_verified=True,
    )
    assert request.create_update_dict() == {
        "email": "child@example.com",
        "password": "unchanged password ",
        "name": "자녀",
    }
    assert "unchanged password" not in repr(request)


@pytest.mark.parametrize(
    "invalid",
    [
        {"email": "invalid"},
        {"password": ""},
        {"name": "  "},
        {"name": "가" * 101},
        {"role": "PARENT"},
        {"user_id": str(uuid4())},
    ],
)
def test_invalid_registration_fields(invalid: dict[str, str]) -> None:
    """잘못된 입력과 서버에서 지정할 필드를 거부합니다."""
    data = {"email": "child@example.com", "password": "password", "name": "자녀"}
    with pytest.raises(ValidationError):
        UserCreate.model_validate(data | invalid)


def test_user_response_excludes_password_from_orm() -> None:
    """ORM 계정을 변환할 때 비밀번호 해시가 응답에 포함되지 않습니다."""
    user = User(
        id=uuid4(),
        email="child@example.com",
        name="자녀",
        hashed_password="secret-hash",
        is_active=True,
        is_superuser=False,
        is_verified=False,
    )
    response = UserRead.model_validate(user).model_dump(mode="json")
    assert response == {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "is_active": True,
        "is_superuser": False,
        "is_verified": False,
    }


@pytest.mark.parametrize("role", ["PARENT", "CHILD"])
def test_invitation_role(role: str) -> None:
    """합의된 역할 값을 초대 요청으로 받습니다."""
    assert InvitationCreate.model_validate({"role": role}).role == FamilyRole(role)


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"role": "father"},
        {"role": "CHILD", "family_id": str(uuid4())},
        {"role": "CHILD", "expires_at": "2099-01-01T00:00:00Z"},
        {"role": "CHILD", "is_representative": True},
    ],
)
def test_invalid_invitation_fields(invalid: dict[str, object]) -> None:
    """역할 외 가족·만료·대표 여부를 클라이언트가 지정하지 못하게 합니다."""
    with pytest.raises(ValidationError):
        InvitationCreate.model_validate(invalid)


def test_invitation_responses_filter_internal_fields() -> None:
    """초대 링크는 생성 응답에만 두고 내부 해시·이력은 공개하지 않습니다."""
    data = {
        "id": uuid4(),
        "family_id": uuid4(),
        "role": FamilyRole.PARENT,
        "invite_url": "https://example.com/invite/share-token",
        "expires_at": datetime.now(UTC),
        "representative_name": "자녀",
        "token_hash": "secret-hash",
        "accepted_by_user_id": uuid4(),
    }
    created = InvitationCreated.model_validate(data).model_dump(mode="json")
    preview = InvitationPreview.model_validate(data).model_dump(mode="json")
    assert set(created) == {"id", "family_id", "role", "invite_url", "expires_at"}
    assert set(preview) == {"family_id", "representative_name", "role", "expires_at"}
    assert created["role"] == "PARENT"
    assert preview["representative_name"] == "자녀"


def test_response_timestamps_require_timezone() -> None:
    """UTC 기반 응답에서 타임존 없는 시각을 거부합니다."""
    with pytest.raises(ValidationError):
        FamilyRead(id=uuid4(), created_at=datetime(2026, 10, 30))
    with pytest.raises(ValidationError):
        InvitationPreview(
            family_id=uuid4(),
            representative_name="자녀",
            role=FamilyRole.PARENT,
            expires_at=datetime(2026, 10, 30),
        )


def test_flat_family_member_response() -> None:
    """서비스에서 조립한 평면 데이터로도 멤버 응답을 만들 수 있습니다."""
    member = FamilyMemberRead.model_validate(
        {
            "id": uuid4(),
            "user_id": uuid4(),
            "name": "자녀",
            "role": "CHILD",
            "is_representative": True,
            "joined_at": datetime.now(UTC),
        }
    )
    assert member.name == "자녀"
    assert member.model_dump(mode="json")["role"] == "CHILD"


def test_family_response_from_persisted_members(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """명시적으로 읽은 멤버 계정에서 이름을 추출해 가족 상세를 구성합니다."""

    async def scenario() -> None:
        user = User(email="child@example.com", name="자녀", hashed_password="secret")
        session.add(user)
        await session.flush()
        family = await FamilyRepository(session).create()
        members = FamilyMemberRepository(session)
        await members.create(
            family.id, user.id, FamilyRole.CHILD, is_representative=True
        )
        await session.commit()
        session.expunge_all()
        rows = await members.list_by_family(family.id)
        detail = FamilyDetail(
            **FamilyRead.model_validate(family).model_dump(),
            members=[FamilyMemberRead.model_validate(row) for row in rows],
        )
        response = detail.model_dump(mode="json")
        assert response["members"][0]["name"] == "자녀"
        assert response["members"][0]["role"] == "CHILD"
        assert "user" not in response["members"][0]
        assert "hashed_password" not in detail.model_dump_json()

    runner.run(scenario())
