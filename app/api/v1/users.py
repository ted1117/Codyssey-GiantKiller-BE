from fastapi import APIRouter

from app.core.auth import CurrentUser
from app.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_me(user: CurrentUser) -> UserRead:
    """현재 로그인한 사용자의 계정 정보를 반환합니다."""
    return UserRead.model_validate(user)
