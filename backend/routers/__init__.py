"""Routers package initialization"""

from fastapi import APIRouter

from .alpaca import router as alpaca_router
from .alpha import router as alpha_router
from .analysis import router as analysis_router
from .config import router as config_router
from .feedback import router as feedback_router


router = APIRouter()
router.include_router(analysis_router)
router.include_router(config_router)
router.include_router(alpaca_router)
router.include_router(feedback_router)
router.include_router(alpha_router)

__all__ = ["router"]
