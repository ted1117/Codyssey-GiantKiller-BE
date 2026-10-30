import asyncio
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from httpx import AsyncClient
from sqlalchemy import Connection, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import Base, PushRegistration, User
from app.repositories.push_registration import PushRegistrationRepository
from app.schemas.push import PushRegistrationUpsert
from app.services.push_registration import PushRegistrationService
from tests.conftest import ROOT, migrate
from tests.test_membership import add_user
from tests.test_verification import bearer

PATH = "/api/v1/users/me/push-registrations"


def test_register_multiple_tokens_and_switch_account(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """재등록은 멱등적이고 같은 FID의 계정 전환은 이전 연결을 제거합니다."""

    async def scenario() -> None:
        first, second = await add_user(session), await add_user(session)
        first_id, second_id = first.id, second.id
        await session.commit()
        for fid in ("app-one", "app-two", "app-one"):
            response = await client.put(
                PATH, headers=bearer(first_id), json={"fid": fid}
            )
            assert response.status_code == 204 and response.content == b""
        repository = PushRegistrationRepository(session)
        assert len(await repository.list_by_user(first_id)) == 2
        await session.rollback()
        response = await client.put(
            PATH, headers=bearer(second_id), json={"fid": "app-one"}
        )
        assert response.status_code == 204
        assert [r.fid for r in await repository.list_by_user(first_id)] == ["app-two"]
        assert [r.fid for r in await repository.list_by_user(second_id)] == ["app-one"]

    runner.run(scenario())


def test_replace_and_delete_only_own_tokens(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """FID 교체·해제는 자기 FID만 변경하고 없는 FID의 해제도 성공합니다."""

    async def scenario() -> None:
        own, other = await add_user(session), await add_user(session)
        own_id, other_id = own.id, other.id
        repository = PushRegistrationRepository(session)
        for user_id, fid in (
            (own_id, "old"),
            (own_id, "second"),
            (other_id, "other"),
        ):
            await repository.upsert(user_id, fid)
        await session.commit()
        for previous in ("old", "other", "new"):
            response = await client.put(
                PATH,
                headers=bearer(own_id),
                json={"fid": "new", "previous_fid": previous},
            )
            assert response.status_code == 204
        for fid in ("new", "new", "missing", "other"):
            response = await client.request(
                "DELETE", PATH, headers=bearer(own_id), json={"fid": fid}
            )
            assert response.status_code == 204 and response.content == b""
        assert [r.fid for r in await repository.list_by_user(own_id)] == ["second"]
        assert [r.fid for r in await repository.list_by_user(other_id)] == ["other"]

    runner.run(scenario())


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_authentication_and_private_validation_errors(
    method: str, client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """인증·활성 계정을 요구하고 검증 오류에도 FID를 노출하지 않습니다."""

    async def scenario() -> None:
        user = await add_user(session)
        user_id = user.id
        await session.commit()
        body = {"fid": "private-fid"}
        assert (await client.request(method, PATH, json=body)).status_code == 401
        for invalid in (
            {},
            {"fid": ""},
            {"fid": "  "},
            {"fid": "x" * 2049},
            {"fid": "한" * 1000},
            {"fid": "private-fid", "user_id": str(uuid4())},
            {"token": "legacy-registration-token"},
            {"fid": "private-fid", "previous_token": "legacy-registration-token"},
        ):
            response = await client.request(
                method, PATH, headers=bearer(user_id), json=invalid
            )
            assert response.status_code == 422
            assert "private-fid" not in response.text
            assert all("input" not in item for item in response.json()["detail"])
        user.is_active = False
        await session.commit()
        assert (
            await client.request(method, PATH, headers=bearer(user_id), json=body)
        ).status_code == 401

    runner.run(scenario())


def test_registration_is_atomic(
    session: AsyncSession, runner: asyncio.Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이전 FID 삭제 실패가 새 FID 등록도 롤백합니다."""

    async def scenario() -> None:
        user = await add_user(session)
        user_id = user.id
        await PushRegistrationRepository(session).upsert(user_id, "old")
        await session.commit()

        async def fail(*args: object) -> None:
            raise RuntimeError("deletion failed")

        monkeypatch.setattr(PushRegistrationRepository, "delete_by_user", fail)
        with pytest.raises(RuntimeError):
            await PushRegistrationService(session).register(
                user_id, PushRegistrationUpsert(fid="new", previous_fid="old")
            )
        assert [
            r.fid
            for r in await PushRegistrationRepository(session).list_by_user(user_id)
        ] == ["old"]

    runner.run(scenario())


@pytest.mark.parametrize(
    ("method", "field"),
    [("PUT", "fid"), ("PUT", "previous_fid"), ("DELETE", "fid")],
)
def test_nul_fid_is_rejected_without_writes_or_disclosure(
    method: str,
    field: str,
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NUL을 두 공통 FID 필드에서 거부하고 DB와 오류 응답에 원문을 남기지 않습니다."""

    async def scenario() -> None:
        user = await add_user(session)
        user_id = user.id
        repository = PushRegistrationRepository(session)
        await repository.upsert(user_id, "original-fid")
        await session.commit()
        body = {"fid": "new-fid", field: "private\x00fid"}
        response = await client.request(
            method, PATH, headers=bearer(user_id), json=body
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert len(detail) == 1 and detail[0]["loc"] == ["body", field]
        assert "NUL" in detail[0]["msg"] and "input" not in detail[0]
        assert "private" not in response.text and "private" not in caplog.text
        assert [r.fid for r in await repository.list_by_user(user_id)] == [
            "original-fid"
        ]

    runner.run(scenario())


def test_concurrent_registration_and_stale_cleanup(
    engine: AsyncEngine, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """동시 등록은 한 행을 유지하고 이전 발송의 정리가 재등록을 삭제하지 않습니다."""

    async def scenario() -> None:
        user = await add_user(session)
        user_id = user.id
        await session.commit()

        async def register() -> None:
            async with AsyncSession(engine) as connection:
                await PushRegistrationService(connection).register(
                    user_id, PushRegistrationUpsert(fid="shared")
                )

        await asyncio.wait_for(asyncio.gather(register(), register()), timeout=5)
        repository = PushRegistrationRepository(session)
        rows = await repository.list_by_user(user_id)
        assert len(rows) == 1
        registration_id, updated_at = rows[0].id, rows[0].updated_at
        await session.commit()
        await register()
        await repository.delete_if_unchanged(registration_id, user_id, updated_at)
        await session.commit()
        session.expire_all()
        current = (await repository.list_by_user(user_id))[0]
        assert current.updated_at > updated_at
        await repository.delete_if_unchanged(
            registration_id, user_id, current.updated_at
        )
        await session.commit()
        assert await repository.list_by_user(user_id) == []

    runner.run(scenario())


def test_push_registration_migration_and_constraints(
    engine: AsyncEngine, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """FID UNIQUE·외래 키와 단계별 마이그레이션 왕복을 검증합니다."""

    def verify(connection: Connection) -> None:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
        user_id = connection.scalar(select(User.id))
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0002_verification")
        assert "push_registrations" not in inspect(connection).get_table_names()
        assert connection.scalar(select(User.id)) == user_id
        migrate(connection)
        assert compare_metadata(context, Base.metadata) == []

    async def scenario() -> None:
        user = await add_user(session)
        user_id = user.id
        await PushRegistrationRepository(session).upsert(user_id, "unique")
        await session.commit()
        for values in (
            {"user_id": user_id, "fid": "unique"},
            {"user_id": uuid4(), "fid": "missing-user"},
        ):
            with pytest.raises(IntegrityError):
                await session.execute(
                    PushRegistration.__table__.insert().values(**values)
                )
            await session.rollback()
        async with engine.begin() as connection:
            await connection.run_sync(verify)

    runner.run(scenario())
