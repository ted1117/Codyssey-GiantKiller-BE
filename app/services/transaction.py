from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncGenerator[None]:
    """인증 조회로 시작된 트랜잭션을 포함해 서비스 작업을 확정하거나 취소합니다."""
    try:
        yield
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
