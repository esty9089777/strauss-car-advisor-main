"""דוח נקודות הבלבול: מזהה נושאים חוזרים, סופר רק באופן אנונימי, ונגיש למנהל/ת בלבד."""
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import insights
from app.auth import AuthenticatedUser, get_current_user
from app.main import app

client = TestClient(app)
ADMIN = AuthenticatedUser(id="admin", email="admin@example.test", role="admin")


def as_admin():
    app.dependency_overrides[get_current_user] = lambda: ADMIN


def say(llm, *messages):
    """שיחה חדשה: כל הודעה של העובד מקבלת תשובה בלי מספרים מהמודל המתוסרט."""
    llm.script = ["הבנתי, אפשר לפרט?"] * len(messages)
    cid = None
    for m in messages:
        r = client.post("/api/chat", json={"conversation_id": cid, "message": m})
        assert r.status_code == 200, r.text
        cid = r.json()["conversation_id"]
    return cid


def test_topics_follow_hebrew_prefixes_without_false_matches():
    assert insights.topics_in("מה זה שווי שימוש?") == ["value_of_use"]
    assert "value_of_use" in insights.topics_in("והשווי שלי עולה?")
    assert "electric" in insights.topics_in("ואם אעבור לחשמלי")
    assert "waiver" in insights.topics_in("אפשר לוותר על הרכב?")
    assert "net_tax" in insights.topics_in("כמה מס אשלם")
    assert insights.topics_in("איזה מסלול ומספר רכב") == []  # "מס" לא נתפס בתוך "מסלול" / "מספר"


def test_report_counts_topics_repeats_and_rep_questions(llm, isolated):
    for _ in range(3):
        say(llm, "לא הבנתי מה זה שווי שימוש", "אז השווי נכנס למס?")
    say(llm, "רוצה לוותר על הרכב")
    as_admin()
    report = client.get("/api/admin/insights").json()

    assert report["conversations"] == 4
    vou = next(t for t in report["topics"] if t["topic"] == "value_of_use")
    assert vou["asked"] == 3 and vou["repeats"] == 3 and vou["conversations"] == 3
    assert vou["action"]
    assert report["topics"][0]["topic"] == "value_of_use"  # שאלות חוזרות = הכי מבלבל
    assert all(t["topic"] != "waiver" for t in report["topics"])  # עלה רק פעם אחת, מתחת לסף


def test_privacy_threshold_and_no_text_stored(llm, isolated):
    for _ in range(2):
        say(llm, "מה עם תלוש השכר הסודי-קסום שלי", "ושוב על התלוש")
    as_admin()
    report = client.get("/api/admin/insights", params={"min_count": 1}).json()
    assert all(t["topic"] != "payslip" for t in report["topics"])  # רק 2 שיחות — לא מוצג
    assert report["hidden_topics"] >= 1
    stored = isolated._con.execute("SELECT * FROM topic_events").fetchall()
    assert "קסום" not in json.dumps(stored, ensure_ascii=False) + json.dumps(report, ensure_ascii=False)


def test_rep_questions_and_time_saving_counts(llm, isolated):
    from tests.test_agent import PROFILE_CALLS

    for _ in range(3):
        llm.script = [PROFILE_CALLS, [("ask_rep", {"car_id": "CAR-10", "question_text": "?"})], "שאלתי את הנציג."]
        client.post("/api/chat", json={"conversation_id": None, "message": "כמה ההשתתפות ב-ATTO 3?"})
    say(llm, "שאלה כללית")
    as_admin()
    report = client.get("/api/admin/insights").json()
    assert report["rep_questions"] == 3
    assert report["conversations_with_rep"] == 3
    assert report["resolved_without_rep"] == 1
    part = next(t for t in report["topics"] if t["topic"] == "participation")
    assert part["rep"] == 3


def test_monthly_snapshot_saved_once_and_readable(llm, isolated):
    for _ in range(3):
        say(llm, "מה זה שווי שימוש")
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    year, mon = map(int, month.split("-"))
    next_month = datetime(year + (mon == 12), mon % 12 + 1, 2, tzinfo=timezone.utc)
    assert insights.snapshot_due(isolated, next_month) == month
    assert insights.snapshot_due(isolated, next_month) is None  # פעם אחת בלבד
    as_admin()
    assert client.get("/api/admin/insights/reports").json()[0]["month"] == month
    saved = client.get(f"/api/admin/insights/reports/{month}").json()
    assert next(t for t in saved["topics"] if t["topic"] == "value_of_use")["conversations"] == 3
    assert client.get("/api/admin/insights/reports/1999-01").status_code == 404
    assert client.get("/api/admin/insights/reports/bad").status_code == 422


def test_only_admin_sees_the_report(isolated):
    assert client.get("/api/admin/insights").status_code == 403  # עובד/ת (ברירת המחדל בבדיקות)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id="r", email="r@example.test", role="rep")
    assert client.get("/api/admin/insights/reports").status_code == 403
    app.dependency_overrides.pop(get_current_user)
    assert client.get("/api/admin/insights").status_code == 401
