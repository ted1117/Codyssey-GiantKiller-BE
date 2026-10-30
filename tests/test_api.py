import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Family, FamilyInvitation, FamilyMember, User

PREFIX = "/api/v1"


async def account(client: AsyncClient) -> tuple[str, dict[str, str]]:
    """가입·로그인해 계정 ID와 Bearer 헤더를 반환합니다."""
    email = f"{uuid4().hex}@example.com"
    response = await client.post(
        f"{PREFIX}/auth/register",
        json={"email": email, "password": "test-password", "name": "자녀"},
    )
    assert response.status_code == 201, response.text
    login = await client.post(
        f"{PREFIX}/auth/login", data={"username": email, "password": "test-password"}
    )
    assert login.status_code == 200, login.text
    return response.json()["id"], {
        "Authorization": f"Bearer {login.json()['access_token']}"
    }


async def family(client: AsyncClient, headers: dict[str, str]) -> str:
    """대표 자녀의 가족을 생성합니다."""
    response = await client.post(f"{PREFIX}/families", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def invitation(
    client: AsyncClient, family_id: str, headers: dict[str, str], role: str = "PARENT"
) -> dict[str, str]:
    """대표가 초대를 발급하고 응답에 수락 경로를 추가합니다."""
    response = await client.post(
        f"{PREFIX}/families/{family_id}/invitations",
        headers=headers,
        json={"role": role},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    data["path"] = urlparse(data["invite_url"]).path
    return data


def test_registration_login_and_authentication(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """안전한 가입, 비밀번호 해시, JWT 만료와 인증 실패를 검증합니다."""

    async def scenario() -> None:
        response = await client.post(
            f"{PREFIX}/auth/register",
            json={
                "email": "Child@example.com",
                "password": "test-password",
                "name": " 자녀 ",
                "is_superuser": True,
                "is_verified": True,
                "is_active": False,
            },
        )
        assert response.status_code == 201, response.text
        user = response.json()
        assert user["name"] == "자녀"
        assert user["email"] == "child@example.com"
        assert (
            user["is_active"] and not user["is_superuser"] and not user["is_verified"]
        )
        assert "password" not in response.text
        stored = await session.get(User, UUID(user["id"]))
        assert stored.hashed_password.startswith("$argon2")
        duplicate = await client.post(
            f"{PREFIX}/auth/register",
            json={"email": "CHILD@example.com", "password": "other", "name": "중복"},
        )
        assert duplicate.status_code == 400
        login = await client.post(
            f"{PREFIX}/auth/login",
            data={"username": "CHILD@example.com", "password": "test-password"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        claims = jwt.decode(token, options={"verify_signature": False})
        assert 3590 <= claims["exp"] - datetime.now(UTC).timestamp() <= 3600
        me = await client.get(
            f"{PREFIX}/users/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 200 and me.json()["id"] == user["id"]
        assert (await client.get(f"{PREFIX}/users/me")).status_code == 401
        assert (
            await client.get(
                f"{PREFIX}/users/me", headers={"Authorization": "Bearer invalid"}
            )
        ).status_code == 401
        bad_login = await client.post(
            f"{PREFIX}/auth/login", data={"username": stored.email, "password": "wrong"}
        )
        assert bad_login.status_code == 400
        expired = jwt.encode(
            {"sub": user["id"], "aud": ["fastapi-users:auth"], "exp": 1},
            "stage3-test-secret-" * 3,
        )
        assert (
            await client.get(
                f"{PREFIX}/users/me", headers={"Authorization": f"Bearer {expired}"}
            )
        ).status_code == 401
        stored.is_active = False
        await session.commit()
        assert (
            await client.get(
                f"{PREFIX}/users/me", headers={"Authorization": f"Bearer {token}"}
            )
        ).status_code == 401

    runner.run(scenario())


def test_family_invitation_end_to_end(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """가입부터 가족 생성·초대·부모 참여·다중 가족 조회까지 연결합니다."""

    async def scenario() -> None:
        representative_id, representative = await account(client)
        parent_id, parent = await account(client)
        assert (await client.get(f"{PREFIX}/families", headers=parent)).json() == []
        family_id = await family(client, representative)
        detail = (
            await client.get(f"{PREFIX}/families/{family_id}", headers=representative)
        ).json()
        assert detail["members"][0]["user_id"] == representative_id
        assert detail["members"][0]["is_representative"] is True
        assert detail["members"][0]["role"] == "CHILD"
        issued = await invitation(client, family_id, representative)
        expires_at = datetime.fromisoformat(issued["expires_at"].replace("Z", "+00:00"))
        assert (
            timedelta(hours=23, minutes=59)
            < expires_at - datetime.now(UTC)
            <= timedelta(hours=24)
        )
        raw_token = issued["path"].rsplit("/", 1)[-1]
        row = await session.get(FamilyInvitation, UUID(issued["id"]))
        assert row.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert raw_token not in row.token_hash
        assert (await client.get(issued["path"])).status_code == 401
        preview = await client.get(issued["path"], headers=parent)
        assert preview.status_code == 200
        assert preview.json()["family_id"] == family_id
        accepted = await client.post(f"{issued['path']}/accept", headers=parent)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["user_id"] == parent_id
        assert accepted.json()["role"] == "PARENT"
        assert accepted.json()["is_representative"] is False
        assert (
            await client.post(f"{issued['path']}/accept", headers=parent)
        ).status_code == 409
        detail = (
            await client.get(f"{PREFIX}/families/{family_id}", headers=parent)
        ).json()
        assert len(detail["members"]) == 2
        assert "hashed_password" not in str(detail)
        other_family = await family(client, representative)
        child_invite = await invitation(client, other_family, representative, "CHILD")
        assert (
            await client.post(f"{child_invite['path']}/accept", headers=parent)
        ).status_code == 200
        families = (await client.get(f"{PREFIX}/families", headers=parent)).json()
        assert {row["id"] for row in families} == {family_id, other_family}

    runner.run(scenario())


def test_family_and_representative_permissions(
    client: AsyncClient, runner: asyncio.Runner
) -> None:
    """타 가족 접근과 일반 멤버의 초대 생성·취소를 차단합니다."""

    async def scenario() -> None:
        _, representative = await account(client)
        _, member = await account(client)
        _, outsider = await account(client)
        family_id = await family(client, representative)
        issued = await invitation(client, family_id, representative, "CHILD")
        assert (
            await client.get(f"{PREFIX}/families/{family_id}", headers=outsider)
        ).status_code == 403
        assert (
            await client.post(
                f"{PREFIX}/families/{family_id}/invitations",
                json={"role": "PARENT"},
                headers=outsider,
            )
        ).status_code == 403
        assert (
            await client.post(f"{issued['path']}/accept", headers=member)
        ).status_code == 200
        assert (
            await client.post(
                f"{PREFIX}/families/{family_id}/invitations",
                json={"role": "PARENT"},
                headers=member,
            )
        ).status_code == 403
        pending = await invitation(client, family_id, representative)
        cancel_path = f"{PREFIX}/families/{family_id}/invitations/{pending['id']}"
        assert (await client.delete(cancel_path, headers=member)).status_code == 403
        assert (await client.delete(cancel_path, headers=outsider)).status_code == 403
        assert (
            await client.get(f"{PREFIX}/families/{uuid4()}", headers=representative)
        ).status_code == 404
        assert (
            await client.get(f"{PREFIX}/invitations/unknown", headers=representative)
        ).status_code == 404
        assert (
            await client.post(
                f"{PREFIX}/invitations/unknown/accept", headers=representative
            )
        ).status_code == 404

    runner.run(scenario())


@pytest.mark.parametrize("unavailable", ["expired", "revoked", "used"])
def test_unavailable_invitations(
    unavailable: str, client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """만료·취소·사용 완료 초대의 조회·수락을 거부합니다."""

    async def scenario() -> None:
        _, representative = await account(client)
        _, recipient = await account(client)
        family_id = await family(client, representative)
        issued = await invitation(client, family_id, representative)
        cancel_path = f"{PREFIX}/families/{family_id}/invitations/{issued['id']}"
        expected = 410
        if unavailable == "expired":
            row = await session.get(FamilyInvitation, UUID(issued["id"]))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        elif unavailable == "revoked":
            assert (
                await client.delete(cancel_path, headers=representative)
            ).status_code == 204
            assert (
                await client.delete(cancel_path, headers=representative)
            ).status_code == 204
        else:
            assert (
                await client.post(f"{issued['path']}/accept", headers=recipient)
            ).status_code == 200
            expected = 409
        assert (
            await client.get(issued["path"], headers=recipient)
        ).status_code == expected
        assert (
            await client.post(f"{issued['path']}/accept", headers=recipient)
        ).status_code == expected
        if unavailable == "used":
            assert (
                await client.delete(cancel_path, headers=representative)
            ).status_code == 409

    runner.run(scenario())


def test_duplicate_member_does_not_consume_invitation(
    client: AsyncClient, runner: asyncio.Runner
) -> None:
    """중복 참여를 거부한 초대는 다른 수신자가 사용할 수 있습니다."""

    async def scenario() -> None:
        _, representative = await account(client)
        _, recipient = await account(client)
        family_id = await family(client, representative)
        issued = await invitation(client, family_id, representative, "CHILD")
        duplicate = await client.post(
            f"{issued['path']}/accept", headers=representative
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "ALREADY_FAMILY_MEMBER"
        assert (
            await client.post(f"{issued['path']}/accept", headers=recipient)
        ).status_code == 200

    runner.run(scenario())


def test_cross_family_invitation_cancellation(
    client: AsyncClient, runner: asyncio.Runner
) -> None:
    """양쪽 가족의 대표여도 경로와 다른 가족의 초대는 취소할 수 없습니다."""

    async def scenario() -> None:
        _, representative = await account(client)
        first = await family(client, representative)
        second = await family(client, representative)
        issued = await invitation(client, first, representative)
        result = await client.delete(
            f"{PREFIX}/families/{second}/invitations/{issued['id']}",
            headers=representative,
        )
        assert result.status_code == 404
        assert (
            await client.get(issued["path"], headers=representative)
        ).status_code == 200

    runner.run(scenario())


def test_concurrent_invitation_acceptance(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """동일 초대의 동시 수락은 한 번만 성공합니다."""

    async def scenario() -> None:
        _, representative = await account(client)
        _, first = await account(client)
        _, second = await account(client)
        family_id = await family(client, representative)
        issued = await invitation(client, family_id, representative)
        responses = await asyncio.gather(
            client.post(f"{issued['path']}/accept", headers=first),
            client.post(f"{issued['path']}/accept", headers=second),
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert await session.scalar(select(func.count()).select_from(FamilyMember)) == 2
        row = await session.get(FamilyInvitation, UUID(issued["id"]))
        assert row.accepted_at is not None and row.revoked_at is None

    runner.run(scenario())


def test_concurrent_parent_limit(client: AsyncClient, runner: asyncio.Runner) -> None:
    """부모가 1명일 때 서로 다른 초대가 동시에 수락돼도 2명을 넘지 않습니다."""

    async def scenario() -> None:
        _, representative = await account(client)
        recipients = [(await account(client))[1] for _ in range(3)]
        family_id = await family(client, representative)
        initial = await invitation(client, family_id, representative)
        assert (
            await client.post(f"{initial['path']}/accept", headers=recipients[0])
        ).status_code == 200
        first = await invitation(client, family_id, representative)
        second = await invitation(client, family_id, representative)
        results = await asyncio.gather(
            client.post(f"{first['path']}/accept", headers=recipients[1]),
            client.post(f"{second['path']}/accept", headers=recipients[2]),
        )
        assert sorted(result.status_code for result in results) == [200, 409]
        denied = next(result for result in results if result.status_code == 409)
        assert denied.json()["detail"] == "PARENT_LIMIT_REACHED"
        detail = (
            await client.get(f"{PREFIX}/families/{family_id}", headers=representative)
        ).json()
        assert sum(member["role"] == "PARENT" for member in detail["members"]) == 2
        blocked = await client.post(
            f"{PREFIX}/families/{family_id}/invitations",
            headers=representative,
            json={"role": "PARENT"},
        )
        assert blocked.status_code == 409
        assert (
            await client.post(
                f"{PREFIX}/families/{family_id}/invitations",
                headers=representative,
                json={"role": "CHILD"},
            )
        ).status_code == 201

    runner.run(scenario())


def test_accept_and_revoke_race(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """수락·취소가 동시에 실행돼도 수락과 취소 중 하나만 확정됩니다."""

    async def scenario() -> None:
        _, representative = await account(client)
        _, recipient = await account(client)
        family_id = await family(client, representative)
        issued = await invitation(client, family_id, representative)
        accepted, revoked = await asyncio.gather(
            client.post(f"{issued['path']}/accept", headers=recipient),
            client.delete(
                f"{PREFIX}/families/{family_id}/invitations/{issued['id']}",
                headers=representative,
            ),
        )
        assert (accepted.status_code, revoked.status_code) in {(200, 409), (410, 204)}
        row = await session.get(FamilyInvitation, UUID(issued["id"]))
        assert (row.accepted_at is None) != (row.revoked_at is None)

    runner.run(scenario())


def test_service_rolls_back_partial_writes(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """가족 생성이나 초대 수락 중 실패하면 선행 쓰기도 롤백됩니다."""
    from app.repositories.family import FamilyMemberRepository
    from app.repositories.invitation import FamilyInvitationRepository

    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("test transaction failure")

    async def scenario() -> None:
        _, representative = await account(client)
        with monkeypatch.context() as patch:
            patch.setattr(FamilyMemberRepository, "create", fail)
            with pytest.raises(RuntimeError):
                await client.post(f"{PREFIX}/families", headers=representative)
        assert await session.scalar(select(func.count()).select_from(Family)) == 0
        await session.rollback()
        family_id = await family(client, representative)
        _, recipient = await account(client)
        issued = await invitation(client, family_id, representative)
        with monkeypatch.context() as patch:
            patch.setattr(FamilyInvitationRepository, "mark_accepted", fail)
            with pytest.raises(RuntimeError):
                await client.post(f"{issued['path']}/accept", headers=recipient)
        assert await session.scalar(select(func.count()).select_from(FamilyMember)) == 1
        row = await session.get(FamilyInvitation, UUID(issued["id"]))
        assert row.accepted_at is None
        await session.rollback()
        assert (
            await client.post(f"{issued['path']}/accept", headers=recipient)
        ).status_code == 200

    runner.run(scenario())


def test_concurrent_registration_same_email(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """중복 검사 이후 가입이 경합해도 하나는 정상적인 중복 응답을 받습니다."""
    from app.repositories.user import UserRepository

    async def scenario() -> None:
        original = UserRepository.get_by_email
        ready = asyncio.Event()
        lookups = 0

        async def synchronized_lookup(
            repository: UserRepository, email: str
        ) -> User | None:
            nonlocal lookups
            found = await original(repository, email)
            lookups += 1
            if lookups == 2:
                ready.set()
            await ready.wait()
            return found

        monkeypatch.setattr(UserRepository, "get_by_email", synchronized_lookup)
        payload = {
            "email": "same@example.com",
            "password": "test-password",
            "name": "자녀",
        }
        async with asyncio.timeout(5):
            results = await asyncio.gather(
                client.post(f"{PREFIX}/auth/register", json=payload),
                client.post(f"{PREFIX}/auth/register", json=payload),
            )
        assert sorted(result.status_code for result in results) == [201, 400]
        denied = next(result for result in results if result.status_code == 400)
        assert denied.json()["detail"] == "REGISTER_USER_ALREADY_EXISTS"
        assert await session.scalar(select(func.count()).select_from(User)) == 1

    runner.run(scenario())


def test_same_user_accepts_two_invitations_concurrently(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """같은 계정이 다른 초대를 동시에 수락해도 소속은 하나만 생성됩니다."""

    async def scenario() -> None:
        _, representative = await account(client)
        _, recipient = await account(client)
        family_id = await family(client, representative)
        first = await invitation(client, family_id, representative, "CHILD")
        second = await invitation(client, family_id, representative, "CHILD")
        results = await asyncio.gather(
            client.post(f"{first['path']}/accept", headers=recipient),
            client.post(f"{second['path']}/accept", headers=recipient),
        )
        assert sorted(result.status_code for result in results) == [200, 409]
        denied = next(result for result in results if result.status_code == 409)
        assert denied.json()["detail"] == "ALREADY_FAMILY_MEMBER"
        assert await session.scalar(select(func.count()).select_from(FamilyMember)) == 2
        used = await session.scalar(
            select(func.count())
            .select_from(FamilyInvitation)
            .where(FamilyInvitation.accepted_at.is_not(None))
        )
        assert used == 1

    runner.run(scenario())


def test_configured_invitation_link_and_openapi(
    application: FastAPI, client: AsyncClient, runner: asyncio.Runner
) -> None:
    """설정한 앱 링크를 사용하며 합의한 API만 노출하는지 확인합니다."""
    from app.core.config import Settings, get_settings

    settings = Settings(
        _env_file=None,
        postgres_password="test",
        auth_secret="s" * 32,
        invitation_base_url="https://app.example.com/invite",
    )
    application.dependency_overrides[get_settings] = lambda: settings

    async def scenario() -> None:
        _, representative = await account(client)
        family_id = await family(client, representative)
        issued = await invitation(client, family_id, representative)
        assert issued["invite_url"].startswith("https://app.example.com/invite/")
        paths = (await client.get("/openapi.json")).json()["paths"]
        assert set(paths) == {
            "/health",
            f"{PREFIX}/auth/register",
            f"{PREFIX}/auth/login",
            f"{PREFIX}/users/me",
            f"{PREFIX}/families",
            f"{PREFIX}/families/{{family_id}}",
            f"{PREFIX}/families/{{family_id}}/invitations",
            f"{PREFIX}/families/{{family_id}}/invitations/{{invitation_id}}",
            f"{PREFIX}/invitations/{{token}}",
            f"{PREFIX}/invitations/{{token}}/accept",
        }
        assert (await client.get("/health")).status_code == 200

    runner.run(scenario())
