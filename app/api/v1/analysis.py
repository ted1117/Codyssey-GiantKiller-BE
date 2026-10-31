from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import AwareDatetime

from app.api.dependencies import AnalysisServiceDep
from app.core.auth import CurrentUser
from app.schemas.analysis import AlertCheck, AlertCheckRead, CallAnalyzeRead


class AnalysisRoute(APIRoute):
    """검증 오류 응답에서 분석 입력 원문을 제외합니다."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """이 분석 API의 검증 오류만 입력 원문 없이 반환합니다."""
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


router = APIRouter(tags=["analysis"], route_class=AnalysisRoute)


@router.post("/alerts/check", response_model=AlertCheckRead, status_code=200)
async def check_alert(
    body: AlertCheck, user: CurrentUser, service: AnalysisServiceDep
) -> AlertCheckRead:
    """부모의 마스킹된 문자를 분석합니다."""
    return await service.check_alert(user.id, body)


@router.post(
    "/calls/analyze",
    response_model=CallAnalyzeRead,
    status_code=200,
    responses={502: {"model": CallAnalyzeRead}},
)
async def analyze_call(
    user: CurrentUser,
    service: AnalysisServiceDep,
    response: Response,
    family_id: Annotated[UUID, Form()],
    occurred_at: Annotated[AwareDatetime, Form()],
    file: Annotated[UploadFile, File()],
) -> CallAnalyzeRead:
    """짧은 녹음을 분석하고 사건 ID와 결과를 반환합니다."""
    result = await service.analyze_call(user.id, family_id, occurred_at, file)
    if result.analysis_status == "FAILED":
        response.status_code = 502
    return result
