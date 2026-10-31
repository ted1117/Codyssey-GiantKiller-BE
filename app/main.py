import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException

from app.api.router import router
from app.core.database import engine, session_factory
from app.core.exceptions import ServiceError
from app.services.push import PushService
from app.services.verification import VerificationService

logger = logging.getLogger(__name__)


async def expire_verifications() -> None:
    """별도 세션으로 만료 요청을 처리하고 30초 후 반복합니다."""
    while True:
        try:
            async with session_factory() as session:
                await VerificationService(
                    session, PushService(session_factory)
                ).expire_due()
        except Exception as error:
            logger.error(
                "verification_expiration_failed error_type=%s", type(error).__name__
            )
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """DB 연결과 만료 작업을 시작하고 작업 종료 후 연결 풀을 해제합니다."""
    expiration_task = None
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        expiration_task = asyncio.create_task(
            expire_verifications(), name="verification-expiration"
        )
        yield
    finally:
        if expiration_task is not None:
            expiration_task.cancel()
            with suppress(asyncio.CancelledError):
                await expiration_task
        await engine.dispose()


app = FastAPI(title="GiantKiller API", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, error: ServiceError) -> JSONResponse:
    """예상한 서비스 오류를 원문 토큰 없이 기록하고 응답으로 변환합니다."""
    if error.status_code == 403:
        logger.warning("permission_denied code=%s", error.detail)
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


@app.exception_handler(HTTPException)
async def authentication_error_handler(
    request: Request, error: HTTPException
) -> Response:
    """인증 실패와 권한 거부를 민감 정보 없이 기록합니다."""
    if error.status_code in {401, 403} or error.detail == "LOGIN_BAD_CREDENTIALS":
        logger.warning("authentication_denied status_code=%s", error.status_code)
    return await http_exception_handler(request, error)
