from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.router import router
from app.core.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="GiantKiller API", lifespan=lifespan)
app.include_router(router)
