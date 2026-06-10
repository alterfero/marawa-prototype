from fastapi import APIRouter
from pydantic import BaseModel

from app.services.health import get_health_status


class HealthResponse(BaseModel):
    status: str


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def read_health() -> HealthResponse:
    return HealthResponse(**get_health_status())

