"""
API endpoints for managing news source configurations.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, List, Any
from pydantic import BaseModel

from database.engine import get_db
from security import require_admin_token
from config.news_sources import (
    get_all_sources, 
    get_enabled_sources,
    NEWS_SOURCES,
    NewsSource
)

router = APIRouter(prefix="/news-sources", tags=["news-sources"])


class NewsSourceUpdate(BaseModel):
    """Model for updating a news source's enabled status."""
    name: str
    enabled: bool


class CategoryUpdate(BaseModel):
    """Model for updating an entire category's enabled status."""
    category: str
    enabled: bool


@router.get("")
async def get_news_sources():
    """
    Get all news sources organized by category.
    Returns the current configuration with enabled/disabled status.
    """
    result = {}
    
    for category, sources in NEWS_SOURCES.items():
        result[category] = {
            "category_name": category,
            "sources": [
                {
                    "name": source.name,
                    "url": source.url,
                    "source_type": source.source_type.value,
                    "category": source.category,
                    "enabled": source.enabled,
                    "priority": source.priority,
                    "fetch_interval_minutes": source.fetch_interval_minutes,
                }
                for source in sources
            ],
            "enabled_count": sum(1 for s in sources if s.enabled),
            "total_count": len(sources),
        }
    
    return result


@router.get("/enabled")
async def get_enabled_news_sources():
    """Get only enabled news sources."""
    enabled = get_enabled_sources()
    
    return {
        source.name: {
            "name": source.name,
            "url": source.url,
            "source_type": source.source_type.value,
            "category": source.category,
        }
        for source in enabled
    }


@router.put("/toggle")
async def toggle_news_source(update: NewsSourceUpdate):
    """
    Toggle a specific news source on/off.
    Note: This updates the in-memory configuration.
    For persistence, would need to save to database or config file.
    """
    # Find and update the source
    for category, sources in NEWS_SOURCES.items():
        for source in sources:
            if source.name == update.name:
                source.enabled = update.enabled
                return {
                    "status": "success",
                    "message": f"News source '{update.name}' {'enabled' if update.enabled else 'disabled'}",
                    "name": update.name,
                    "enabled": update.enabled,
                }
    
    raise HTTPException(status_code=404, detail=f"News source '{update.name}' not found")


@router.put("/toggle-category")
async def toggle_category(update: CategoryUpdate):
    """
    Toggle all sources in a category on/off.
    """
    if update.category not in NEWS_SOURCES:
        raise HTTPException(status_code=404, detail=f"Category '{update.category}' not found")
    
    # Update all sources in the category
    for source in NEWS_SOURCES[update.category]:
        source.enabled = update.enabled
    
    return {
        "status": "success",
        "message": f"Category '{update.category}' {'enabled' if update.enabled else 'disabled'}",
        "category": update.category,
        "enabled": update.enabled,
    }
