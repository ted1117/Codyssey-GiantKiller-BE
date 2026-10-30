from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import FamilyServiceDep
from app.core.auth import CurrentUser
from app.schemas.family import FamilyDetail, FamilyRead

router = APIRouter(prefix="/families", tags=["families"])


@router.post("", response_model=FamilyDetail, status_code=status.HTTP_201_CREATED)
async def create_family(user: CurrentUser, service: FamilyServiceDep) -> FamilyDetail:
    """가족을 만들고 로그인 사용자를 대표 자녀로 등록합니다."""
    return await service.create(user.id)


@router.get("", response_model=list[FamilyRead])
async def list_families(
    user: CurrentUser, service: FamilyServiceDep
) -> list[FamilyRead]:
    """현재 사용자가 참여한 가족 목록을 반환합니다."""
    return await service.list_by_user(user.id)


@router.get("/{family_id}", response_model=FamilyDetail)
async def get_family(
    family_id: UUID, user: CurrentUser, service: FamilyServiceDep
) -> FamilyDetail:
    """소속된 가족의 정보와 멤버를 조회합니다."""
    return await service.get_detail(family_id, user.id)
