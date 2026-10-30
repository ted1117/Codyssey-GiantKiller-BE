from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.dependencies import PushRegistrationServiceDep
from app.core.auth import CurrentUser
from app.schemas.push import PushRegistrationDelete, PushRegistrationUpsert


class PushRegistrationRoute(APIRoute):
    """검증 오류 응답에서 FCM FID를 포함한 입력 원문을 제외합니다."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """이 FID API의 검증 오류만 입력 원문 없이 반환합니다."""
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                details = [
                    {key: item[key] for key in ("type", "loc", "msg")}
                    for item in error.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": details})

        return handle


router = APIRouter(
    prefix="/users/me/push-registrations",
    tags=["push-registrations"],
    route_class=PushRegistrationRoute,
)


@router.put("", status_code=204)
async def register(
    body: PushRegistrationUpsert, user: CurrentUser, service: PushRegistrationServiceDep
) -> Response:
    """로그인한 사용자에게 앱의 현재 FCM FID를 연결합니다."""
    await service.register(user.id, body)
    return Response(status_code=204)


@router.delete("", status_code=204)
async def delete(
    body: PushRegistrationDelete, user: CurrentUser, service: PushRegistrationServiceDep
) -> Response:
    """로그아웃 전에 현재 앱 FID와 사용자의 연결을 해제합니다."""
    await service.delete(user.id, body.fid)
    return Response(status_code=204)
