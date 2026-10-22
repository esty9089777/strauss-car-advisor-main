"""דוח נקודות הבלבול — למנהל/ת בלבד. רק ספירות לפי נושא, בלי טקסט, שמות או סכומים."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query

from app import insights
from app.auth import require_admin
from app.store import get_store

router = APIRouter(prefix="/api/admin/insights", tags=["insights"], dependencies=[Depends(require_admin)])

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("")
def current(min_count: int = Query(insights.DEFAULT_MIN_COUNT, ge=1, le=1000)):
    """הדוח של החודש הנוכחי, מחושב עכשיו ("הפקת דוח עכשיו")."""
    return insights.current_report(get_store(), min_count=min_count)


@router.get("/reports")
def reports():
    """הדוחות החודשיים שנשמרו אוטומטית בתחילת כל חודש."""
    return get_store().monthly_reports()


@router.get("/reports/{month}")
def saved(month: str, min_count: int = Query(insights.DEFAULT_MIN_COUNT, ge=1, le=1000)):
    if not MONTH.fullmatch(month):
        raise HTTPException(422, "month must look like 2026-10")
    counts = get_store().monthly_report(month)
    if counts is None:
        raise HTTPException(404, "No saved report for this month")
    return insights.build_report(month, counts, min_count)
