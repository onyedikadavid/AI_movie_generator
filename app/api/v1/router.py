from fastapi import APIRouter
from app.api.v1.endpoints import projects, health, chat

api_router = APIRouter()
api_router.include_router(health.router, prefix="", tags=["Health"])
api_router.include_router(projects.router, prefix="", tags=["Projects"])
api_router.include_router(chat.router, prefix="", tags=["Chat"])
