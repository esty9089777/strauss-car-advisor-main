"""דוח נקודות הבלבול למנהלים: על מה עובדים שואלים, איפה הם נתקעים, וכמה זמן הבוט חוסך.

פרטיות: לא נשמר שום טקסט, שם או סכום. מכל הודעה נשמרים רק הנושאים שלה (מרשימה קבועה), עם גיבוב
של מזהה השיחה כדי לספור שיחות שונות. הספירות שורדות את מחיקת השיחות אחרי 30 יום, ונושא שעלה בפחות
מ-MIN_CONVERSATIONS שיחות לא מוצג בכלל — כך שאי אפשר לזהות עובד/ת.

הזיהוי לפי מילות מפתח ולא בעזרת מודל שפה: מהיר, חינמי, ניתן להסבר ולבדיקה.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MIN_CONVERSATIONS = 3  # סף פרטיות: נושא שעלה בפחות שיחות לא מוצג
DEFAULT_MIN_COUNT = 3

# לכל נושא: שם, פעולה מוצעת למנהל, ומילות מפתח. "*" בסוף מילה = כל המשכה (חשמל* → חשמלי, חשמלית)
TOPICS: dict[str, dict] = {
    "value_of_use": {
        "label": "שווי שימוש",
        "action": "להוסיף לעובדים הסבר קצר עם דוגמה: מה זה שווי שימוש ואיך הוא מוריד את הנטו",
        "words": ["שווי", "זקיפה", "זקיפת"],
    },
    "participation": {
        "label": "השתתפות ברכב",
        "action": "לפרסם טבלת השתתפויות לפי דגם — רוב השאלות לנציג ייעלמו",
        "words": ["השתתפות", "משתתף*", "להשתתף"],
    },
    "electric": {
        "label": "רכב חשמלי והחזר חשמל",
        "action": "להבהיר בדף מידע את החזר החשמל: 250 ₪ נטו בחודש, מגולם",
        "words": ["חשמל*", "טעינה", "היברידי*", "פלאג*"],
    },
    "waiver": {
        "label": "ויתור על הרכב ואחזקה",
        "action": "לפרסם אילו עיסוקים יכולים לוותר על הרכב ובכמה אחזקה",
        "words": ["ויתור", "לוותר", "אוותר", "מוותר*", "אחזקה", "אחזקת"],
    },
    "family": {
        "label": "מסלול ברוכת ילדים",
        "action": "להבהיר את תנאי המסלול המשפחתי ואת חלוקת הפער 50/50",
        "words": ["ילד*", "משפחתי*", "ברוכת", "קרניבל", "סנטה"],
    },
    "eligibility": {
        "label": "זכאות, קבוצה ותקציב",
        "action": "לפרסם את טבלת הקבוצות והתקציבים, ומאיזו קבוצה מותר לבחור",
        "words": ["זכאי*", "זכאות", "קבוצה", "קבוצת", "תקציב*", "מגיע"],
    },
    "net_tax": {
        "label": "נטו, מס וניכויים",
        "action": "להוסיף הסבר על רכיבי התלוש שמשתנים כשמחליפים רכב",
        "words": ["נטו", "מס", "מסים", "מיסים", "ברוטו", "ביטוח", "זיכוי", "פנסיה", "השתלמות"],
    },
    "payslip": {
        "label": "תלוש שכר",
        "action": "להבהיר אילו נתונים מהתלוש צריך, ואיפה מוצאים אותם",
        "words": ["תלוש*"],
    },
    "current_vehicle": {
        "label": "זיהוי הרכב הנוכחי",
        "action": "לאפשר בחירת הרכב הנוכחי לפי מספר רישוי, בלי לנחש רמת גימור",
        "words": [],  # מגיע רק משאלות לנציג
    },
    "other": {"label": "אחר", "action": "לעבור על השאלות הפתוחות לנציג", "words": []},
}

_PREFIX = "[והבלשכמ]{0,2}"  # אותיות שימוש: "והשווי", "בחשמלי", "שהתקציב"


def _pattern(words: list[str]) -> re.Pattern | None:
    alts = [re.escape(w[:-1]) + "[א-ת]*" if w.endswith("*") else re.escape(w) for w in words]
    return re.compile(rf"(?<![א-ת]){_PREFIX}(?:{'|'.join(alts)})(?![א-ת])") if alts else None


_PATTERNS = {key: _pattern(t["words"]) for key, t in TOPICS.items()}


def topics_in(text: str) -> list[str]:
    return [key for key, p in _PATTERNS.items() if p and p.search(text)]


def rep_topic(field: str) -> str:
    if field.startswith("participation"):
        return "participation"
    return field if field in TOPICS else "other"


def _month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def _hash(conversation_id: str) -> str:
    return hashlib.sha256(conversation_id.encode()).hexdigest()[:16]


def record_message(store, conversation_id: str, text: str, already_asked: list[str], is_first: bool) -> list[str]:
    """נושאי ההודעה. נושא שכבר עלה באותה שיחה = 'repeat' — הסימן הכי חזק שמשהו לא הובן.

    מחזיר את רשימת הנושאים המעודכנת של השיחה. כשל ברישום לא עוצר את השיחה.
    """
    topics = topics_in(text)
    events = [("conversation", "")] if is_first else []
    events += [("repeat" if t in already_asked else "asked", t) for t in topics]
    _add(store, conversation_id, events)
    return list(dict.fromkeys(already_asked + topics))


def record_rep_question(store, conversation_id: str, field: str) -> None:
    _add(store, conversation_id, [("rep", rep_topic(field))])


def record_guard_block(store, conversation_id: str, topics: list[str]) -> None:
    _add(store, conversation_id, [("guard", t) for t in topics] or [("guard", "other")])


def _add(store, conversation_id: str, events: list[tuple]) -> None:
    if not events:
        return
    try:
        store.add_topic_events(_month(), _hash(conversation_id), events)
    except Exception:  # הדוח הוא תוספת — אסור שתקלה בו תפיל שיחה
        log.exception("רישום נושאים לדוח נכשל")


def build_report(month: str, counts: list[tuple], min_count: int = DEFAULT_MIN_COUNT) -> dict:
    """counts: (kind, topic, events, distinct_conversations) מה-Store, לחודש אחד."""
    by_topic: dict[str, dict] = {}
    totals = {"conversations": 0, "conversations_with_rep": 0, "rep_questions": 0, "guard_blocks": 0}
    for kind, topic, events, conversations in counts:
        if kind == "conversation":
            totals["conversations"] = conversations
            continue
        if kind == "rep_any":
            totals["conversations_with_rep"] = conversations
            continue
        if kind == "rep":
            totals["rep_questions"] += events
        if kind == "guard":
            totals["guard_blocks"] += events
        t = by_topic.setdefault(topic, {"asked": 0, "repeats": 0, "rep": 0, "guard": 0, "conversations": 0})
        t[{"asked": "asked", "repeat": "repeats", "rep": "rep", "guard": "guard"}[kind]] += events
        t["conversations"] = max(t["conversations"], conversations)

    topics, hidden = [], 0
    for key, t in by_topic.items():
        mentions = t["asked"] + t["repeats"] + t["rep"]
        if mentions < min_count:
            continue
        if t["conversations"] < MIN_CONVERSATIONS:
            hidden += 1
            continue
        # ציון בלבול: שאלה חוזרת, שאלה לנציג ותשובה שנחסמה שוקלות כפול משאלה רגילה
        score = t["asked"] + 2 * (t["repeats"] + t["rep"] + t["guard"])
        topics.append({"topic": key, "label": TOPICS.get(key, TOPICS["other"])["label"],
                       "action": TOPICS.get(key, TOPICS["other"])["action"], "score": score, **t})
    topics.sort(key=lambda t: -t["score"])
    resolved = max(0, totals["conversations"] - totals["conversations_with_rep"])
    return {
        "month": month,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_count": min_count,
        "min_conversations": MIN_CONVERSATIONS,
        **totals,
        "resolved_without_rep": resolved,
        "topics": topics,
        "hidden_topics": hidden,
    }


def current_report(store, month: str | None = None, min_count: int = DEFAULT_MIN_COUNT) -> dict:
    month = month or _month()
    return build_report(month, store.topic_counts(month), min_count)


def snapshot_due(store, now: datetime | None = None) -> str | None:
    """בתחילת חודש: שומר את הספירות של החודש שהסתיים כדוח באזור האישי של המנהל/ת (פעם אחת)."""
    now = now or datetime.now(timezone.utc)
    first = now.replace(day=1)
    previous = (first.replace(month=12, year=first.year - 1) if first.month == 1 else first.replace(month=first.month - 1)).strftime("%Y-%m")
    if store.monthly_report(previous) is not None:
        return None
    counts = store.topic_counts(previous)
    if not counts:
        return None
    store.save_monthly_report(previous, json.dumps(counts))
    return previous
