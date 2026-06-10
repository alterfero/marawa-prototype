from fastapi import APIRouter

from app.api.routes.curation import router as curation_router
from app.api.routes.dataset import router as dataset_router
from app.api.routes.exploration import router as exploration_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.search import router as search_router
from app.api.routes.stories import router as stories_router
from app.api.routes.tropes import router as tropes_router
from app.api.routes.visualizations import router as visualizations_router


api_router = APIRouter()
api_router.include_router(curation_router)
api_router.include_router(dataset_router)
api_router.include_router(exploration_router)
api_router.include_router(health_router)
api_router.include_router(jobs_router)
api_router.include_router(search_router)
api_router.include_router(stories_router)
api_router.include_router(tropes_router)
api_router.include_router(visualizations_router)
