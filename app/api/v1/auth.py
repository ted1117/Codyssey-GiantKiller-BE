from fastapi import APIRouter

from app.core.auth import auth_backend, fastapi_users
from app.schemas.user import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(fastapi_users.get_register_router(UserRead, UserCreate))
login_router = fastapi_users.get_auth_router(auth_backend)
login_router.routes = [route for route in login_router.routes if route.path == "/login"]
router.include_router(login_router)
