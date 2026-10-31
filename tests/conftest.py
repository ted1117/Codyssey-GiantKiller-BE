import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

ROOT = Path(__file__).resolve().parents[1]


def migrate(connection: Connection, target: str = "head") -> None:
    """테스트 전용 연결에 마이그레이션을 적용하거나 되돌립니다."""
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    if target == "base":
        command.downgrade(config, target)
    else:
        command.upgrade(config, target)


@pytest.fixture
def runner() -> Iterator[asyncio.Runner]:
    """테스트별로 하나의 비동기 실행 루프를 제공합니다."""
    with asyncio.Runner() as runner:
        yield runner


@pytest.fixture
def engine(runner: asyncio.Runner) -> Iterator[AsyncEngine]:
    """별도 PostgreSQL 스키마에 초기 마이그레이션을 적용합니다."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL에 테스트용 PostgreSQL 주소가 필요합니다")
    schema = f"test_membership_{uuid4().hex}"
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )

    async def setup() -> None:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(migrate)

    async def cleanup() -> None:
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            await engine.dispose()

    try:
        runner.run(setup())
        yield engine
    finally:
        runner.run(cleanup())


@pytest.fixture
def session(engine: AsyncEngine, runner: asyncio.Runner) -> Iterator[AsyncSession]:
    """테스트별 세션을 제공하고 미완료 트랜잭션을 정리합니다."""
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        runner.run(session.close())


@pytest.fixture
def application(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    """실제 앱의 설정·요청 세션·lifespan을 테스트 DB로 교체합니다."""
    from app.core.config import Settings, get_settings

    # 개발용 .env를 읽지 않고 테스트 전용 값으로 앱을 초기화한다.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    monkeypatch.setenv("AUTH_SECRET", "stage3-test-secret-" * 3)
    get_settings.cache_clear()

    from app import main
    from app.api.dependencies import get_push_service
    from app.core.database import get_session
    from app.main import app
    from app.services.push import PushService

    test_sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "session_factory", test_sessions)

    settings = Settings(
        _env_file=None,
        postgres_password="test-only",
        auth_secret="stage3-test-secret-" * 3,
    )

    async def get_test_session() -> AsyncIterator[AsyncSession]:
        async with test_sessions() as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[get_settings] = lambda: settings
    # API 테스트가 실제 FCM이나 개발 DB에 접근하지 않도록 발송을 기록만 한다.
    push = AsyncMock(spec=PushService)
    app.dependency_overrides[get_push_service] = lambda: push
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


@pytest.fixture
def client(application: FastAPI, runner: asyncio.Runner) -> Iterator[AsyncClient]:
    """실제 FastAPI 라우터를 호출하는 비동기 클라이언트를 제공합니다."""
    client = AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    )
    try:
        yield client
    finally:
        runner.run(client.aclose())
