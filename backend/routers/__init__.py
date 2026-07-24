"""Routers package initialization"""

from fastapi import APIRouter

# Import all routers for export
# Note: Routers are included with prefixes in main.py
from .alpaca import router as alpaca_router
from .alpha import router as alpha_router
from .analysis import router as analysis_router
from .config import router as config_router
from .feedback import router as feedback_router
from .news import router as news_router
from .edgar import router as edgar_router
from .news_sources import router as news_sources_router

# Main router (for backward compatibility)
router = APIRouter()

__all__ = ["router", "alpaca_router", "alpha_router", "analysis_router", "config_router", 
           "feedback_router", "news_router", "edgar_router", "news_sources_router"]
