from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import EventQueryServiceDep, EventStatusServiceDep
from app.core.auth import CurrentUser
from app.schemas.event import EventRead, EventStatusUpdate

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventRead], status_code=200)
async def list_events(
    family_id: UUID,
    user: CurrentUser,
    service: EventQueryServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EventRead]:
    """가족 자녀에게 공개 사건을 최신순으로 반환합니다."""
    return await service.list_by_family(family_id, user.id, limit, offset)


@router.get("/{event_id}", response_model=EventRead, status_code=200)
async def get_event(
    event_id: UUID, user: CurrentUser, service: EventQueryServiceDep
) -> EventRead:
    """가족 자녀에게 사건의 최소 상세 정보를 반환합니다."""
    return await service.get_detail(event_id, user.id)


@router.patch("/{event_id}/status", response_model=EventRead, status_code=200)
async def update_event_status(
    event_id: UUID,
    body: EventStatusUpdate,
    user: CurrentUser,
    service: EventStatusServiceDep,
) -> EventRead:
    """가족 자녀가 사건을 확인함 또는 해결됨으로 변경합니다."""
    return await service.update(event_id, user.id, body)
