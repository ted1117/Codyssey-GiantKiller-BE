from fastapi import APIRouter

from app.api.v1 import (
    actions,
    analysis,
    auth,
    events,
    families,
    invitations,
    push_registrations,
    users,
    verify,
)

router = APIRouter()
router.include_router(actions.router, prefix="/api/v1")
router.include_router(analysis.router, prefix="/api/v1")
router.include_router(events.router, prefix="/api/v1")
router.include_router(auth.router, prefix="/api/v1")
router.include_router(users.router, prefix="/api/v1")
router.include_router(families.router, prefix="/api/v1")
router.include_router(invitations.router, prefix="/api/v1")
router.include_router(verify.router, prefix="/api/v1")
router.include_router(push_registrations.router, prefix="/api/v1")


@router.get("/health")
async def health():
    return {"status": "ok"}
