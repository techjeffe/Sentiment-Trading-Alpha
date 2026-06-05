"""
Feedback Analysis API Router.

Endpoints:
- GET  /api/feedback/latest     — Get the latest feedback analysis
- POST /api/feedback/analyze    — Trigger immediate analysis
- POST /api/feedback/accept     — User accepts parameter adjustments
- GET  /api/feedback/schedule   — Get the next scheduled run time
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.engine import get_db, get_decision_log_db
from services.feedback_analysis import (
    run_weekly_analysis,
    should_run_analysis,
    _next_friday_4pm_ct,
)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class AcceptAdjustmentRequest(BaseModel):
    """Request to accept parameter adjustments from feedback analysis."""
    suggestion_ids: list[str] = Field(..., description="List of suggestion IDs to accept")
    request_id: str = Field("", description="Optional request ID for tracking")


class AcceptAdjustmentResponse(BaseModel):
    status: str
    accepted: list[str]
    message: str


class ScheduleResponse(BaseModel):
    next_run: str
    is_friday: bool
    is_within_window: bool


@router.get("/latest", summary="Get latest feedback analysis")
def get_latest_feedback(
    db: Session = Depends(get_decision_log_db),
) -> Dict[str, Any]:
    """Return the latest weekly feedback analysis result."""
    from database.models import DecisionLogFeedback

    latest = db.query(DecisionLogFeedback).filter(
        DecisionLogFeedback.feedback_type == "weekly_analysis"
    ).order_by(DecisionLogFeedback.created_at.desc()).first()

    if not latest or not latest.analysis_data:
        return {
            "status": "no_data",
            "message": "No feedback analysis available yet.",
            "summary": {},
            "patterns": [],
            "suggestions": [],
            "confidence": 0.0,
        }

    try:
        analysis = __import__("json").loads(latest.analysis_data)
    except Exception:
        analysis = {"status": "error", "message": "Could not parse analysis data"}

    return {**analysis, "analyzed_at": latest.created_at.isoformat()}


@router.post("/analyze", summary="Trigger immediate feedback analysis", response_model=Dict[str, Any])
def trigger_analysis(
    db: Session = Depends(get_db),
    request_id: str = "",
) -> Dict[str, Any]:
    """Run feedback analysis immediately (normally auto-runs Friday 4pm CT)."""
    try:
        result = run_weekly_analysis(db, request_id or "manual_trigger")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feedback analysis failed: {str(e)}")


@router.post("/accept", summary="Accept parameter adjustments", response_model=AcceptAdjustmentResponse)
def accept_adjustments(
    request: AcceptAdjustmentRequest,
    db: Session = Depends(get_decision_log_db),
) -> AcceptAdjustmentResponse:
    """
    User accepts specific parameter adjustments suggested by the feedback analysis.
    
    The accepted suggestions are logged and will be applied on the next analysis run.
    This does NOT change the running system — changes take effect after the next
    analysis cycle or when the user explicitly saves them via the Admin UI.
    """
    from database.models import DecisionLogFeedback

    # Log the user's acceptance
    acceptance = DecisionLogFeedback(
        feedback_type="user_acceptance",
        analysis_data=__import__("json").dumps({
            "accepted_suggestions": request.suggestion_ids,
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "request_id": request.request_id,
        }, default=str),
        created_at=datetime.now(timezone.utc),
    )
    db.add(acceptance)
    db.commit()

    return AcceptAdjustmentResponse(
        status="accepted",
        accepted=request.suggestion_ids,
        message=f"Accepted {len(request.suggestion_ids)} suggestion(s). Changes will apply after next analysis cycle.",
    )


@router.get("/schedule", summary="Get next scheduled run time", response_model=ScheduleResponse)
def get_schedule(
    db: Session = Depends(get_db),
) -> ScheduleResponse:
    """Return when the next weekly analysis will run."""
    now_ct = _next_friday_4pm_ct()
    now = __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("America/Chicago"))
    is_friday = now.weekday() == 4
    is_within_window = is_friday and 16 <= now.hour < 18

    return ScheduleResponse(
        next_run=now_ct.isoformat(),
        is_friday=is_friday,
        is_within_window=is_within_window,
    )


@router.get("/status", summary="Check if analysis should run now")
def check_status() -> Dict[str, Any]:
    """Check if the weekly analysis should run at this moment."""
    should = should_run_analysis()
    next_run = _next_friday_4pm_ct()

    return {
        "should_run_now": should,
        "next_scheduled_run": next_run.isoformat(),
        "current_time_ct": datetime.now(__import__("zoneinfo").ZoneInfo("America/Chicago")).isoformat(),
    }
