"""הכלים של הסוכן. כל כלי מקבל את רשומת השיחה, מאמת את הקלט, ומחזיר dict קטן שהמודל קורא.

עיקרון: הכלים הם המקום היחיד שבו נכנס מידע לפרופיל ומספרים לשיחה. המודל רק בוחר איזה כלי להפעיל.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import uuid4

from app import insights, seed
from app.contracts import CompareRequest, CompareResponse, OptionRequest, RepQuestion, Tracked
from app.engine import vou
from app.engine.compare import DRIVE_LABELS, compare
from app.engine.eligibility import check_car
from app.engine.rules import load_rules
from app.store import ConversationRecord, Store

PROFILE_NUMBERS = ["base_salary", "commissions", "bonus", "pension_pct", "study_fund_pct", "credit_points"]

TOOL_SPECS = [
    {
        "name": "update_profile",
        "description": "שמירת נתונים שהעובד מסר במפורש. לקרוא מיד כשהעובד אומר נתון. לעולם לא להמציא ערך.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "שם פרטי של העובד, אם אמר"},
                "occupation": {"type": "string", "description": "שם העיסוק בדיוק כמו ברשימת העיסוקים"},
                "group": {"type": "string", "enum": ["א", "ב", "ג", "ד"], "description": "רק אם העיסוק לא ידוע"},
                "base_salary": {"type": "number", "description": "שכר בסיס חודשי ברוטו בש״ח"},
                "commissions": {"type": "number", "description": "עמלות חודשיות בש״ח (0 אם אין)"},
                "bonus": {"type": "number", "description": "בונוס חודשי בש״ח (0 אם אין)"},
                "pension_pct": {"type": "number", "description": "אחוז הפרשת העובד לפנסיה, למשל 6"},
                "study_fund_pct": {"type": "number", "description": "אחוז הפרשת העובד לקרן השתלמות, למשל 2.5 (0 אם אין)"},
                "credit_points": {"type": "number", "description": "נקודות זיכוי, למשל 2.25"},
                "children_under_23": {"type": "integer", "description": "מספר הילדים עד גיל 23"},
            },
        },
    },
    {
        "name": "set_current_situation",
        "description": (
            "מה יש לעובד היום: רכב והשתתפותו בו, או ויתור (אחזקת רכב). "
            "רכב מהקטלוג — car_id. רכב שלא בקטלוג — vou_key מתוצאת find_vehicle (רק אחרי שזוהה חד-משמעית)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["car", "waiver"]},
                "car_id": {"type": "string", "description": "מזהה מהקטלוג, למשל CAR-09"},
                "vou_key": {"type": "string", "description": "מפתח מהמאגר מתוך find_vehicle, למשל 2026/2021/1/00413/00296"},
                "vehicle_description": {
                    "type": "string",
                    "description": "כשהרכב עוד לא זוהה חד-משמעית: מה שהעובד אמר (למשל 'טויוטה קורולה 2021'). ואז ask_rep about=current_vehicle",
                },
                "participation": {"type": "number", "description": "השתתפות חודשית היום בש״ח, כפי שהעובד אמר"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "find_vehicle",
        "description": (
            "חיפוש הרכב של היום במאגר רשות המסים (כל הרכבים, לא רק הקטלוג). "
            "query: יצרן ו/או דגם — כינוי הדגם באנגלית כמו ברישיון (COROLLA, OCTAVIA), או קוד הדגם מהרישיון. "
            "לרוב יחזרו כמה רמות גימור עם שווי שימוש שונה: אסור לבחור אחת לבד — מציגים לעובד, "
            "ואם לא יודע — ask_rep (הנציג מזהה לפי מספר הרישוי)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "registration_year": {"type": "integer", "description": "שנת רישום (עלייה לכביש)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_catalog",
        "description": "קטלוג הרכבים עם זכאות לפי הפרופיל. לחפש כאן את car_id לפי שם הדגם שהעובד אמר.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "compare_options",
        "description": (
            "מריץ את מנוע החישוב: נטו היום מול כל חלופה. כל המספרים שמותר להגיד לעובד מגיעים מכאן. "
            "participation: מה שהעובד אמר על ההשתתפות ברכב החדש; null אם לא ידוע (ואז לשאול את הנציג עם ask_rep)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["car", "waiver"]},
                            "car_id": {"type": "string"},
                            "participation": {"type": ["number", "null"]},
                        },
                        "required": ["kind"],
                    },
                }
            },
            "required": ["options"],
        },
    },
    {
        "name": "ask_rep",
        "description": (
            "שליחת שאלה לנציג התחבורה כשחסר מידע שהעובד לא יודע (בעיקר השתתפות ברכב חדש). "
            "מחזיר גם השוואה חלקית לבינתיים (comparison_meanwhile): הנטו היום, והחלופה תתעדכן כשהנציג יענה."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "car_id": {"type": "string", "description": "רכב מהקטלוג שעליו השאלה (השתתפות ברכב חדש)"},
                "about": {
                    "type": "string",
                    "enum": ["current_vehicle"],
                    "description": "current_vehicle = לא ברור איזה רכב יש לעובד היום (איזו רמת גימור); הנציג יזהה לפי מספר הרישוי",
                },
                "question_text": {"type": "string", "description": "השאלה לנציג, בעברית. לכלול את מה שהעובד כן אמר (יצרן, דגם, שנה)"},
            },
            "required": ["question_text"],
        },
    },
]


class ToolError(Exception):
    pass


def _catalog() -> dict[str, dict]:
    return {c["id"]: c for c in seed.catalog()}


def _occupations() -> dict[str, dict]:
    return {o["name"]: o for o in seed.occupations()}


def _tracked(value):
    return Tracked(value=value, source="employee", confirmed=True)


def update_profile(record: ConversationRecord, store: Store, **fields) -> dict:
    profile = record.state.profile
    saved = {}
    if fields.get("name"):
        record.state.employee_display_name = str(fields.pop("name")).strip()[:40]
        saved["name"] = record.state.employee_display_name
    if "occupation" in fields:
        name = fields.pop("occupation")
        if name not in _occupations():
            raise ToolError(f"עיסוק לא מוכר: {name}. העיסוקים האפשריים: {', '.join(_occupations())}")
        profile.occupation = _tracked(name)
        saved["occupation"] = name
        saved["group_from_occupation"] = _occupations()[name]["group"]
    if "group" in fields:
        profile.group = _tracked(fields.pop("group"))
        saved["group"] = profile.group.value
    if "children_under_23" in fields:
        children = int(fields.pop("children_under_23"))
        if children < 0:
            raise ToolError("מספר ילדים לא יכול להיות שלילי")
        profile.children_under_23 = _tracked(children)
        saved["children_under_23"] = children
    for name in PROFILE_NUMBERS:
        if name in fields:
            value = float(fields.pop(name))
            if value < 0 or (name.endswith("_pct") and value > 30):
                raise ToolError(f"ערך לא סביר ל-{name}: {value}")
            setattr(profile, name, _tracked(value))
            saved[name] = value
    return {"saved": saved, "still_missing": missing_profile_fields(record)}


def vehicle_candidate(row: dict) -> dict:
    """רשומה מהמאגר בצורה שעובד יכול לזהות: יצרן, דגם, רמת גימור, נפח מנוע, מחיר מחירון."""
    rules = load_rules()
    return {
        "vou_key": vou.key_text(vou.key_of(row)),
        "name": f"{row['manufacturer']} {row['commercial_name']} ({row['registration_year']})",
        "trim": row["model_description"],
        "model_code": row["model_code"],
        "engine_cc": int(row["engine_cc"]) if str(row["engine_cc"]).isdigit() and int(row["engine_cc"]) else None,
        "drive": rules.value_of_use.drive_field[str(row["drive_code"])],
        "list_price": row["list_price"],
        "value_of_use": row["value_of_use"],
    }


def vehicle_label(row: dict) -> str:
    return f"{row['manufacturer']} {row['commercial_name']} ({row['registration_year']})"


def find_vehicle(record: ConversationRecord, store: Store, query: str, registration_year: int | None = None) -> dict:
    rows, total = vou.search(query, registration_year)
    result = {"total_matches": total, "candidates": [vehicle_candidate(r) for r in rows]}
    if total == 0:
        result["hint"] = "לא נמצא. לנסות את כינוי הדגם באנגלית כמו ברישיון, או בלי שנה"
    elif total > len(rows):
        result["hint"] = f"יש {total} התאמות — לצמצם: שנת רישום, רמת גימור או קוד הדגם מהרישיון"
    elif len({c['value_of_use'] for c in result["candidates"]}) > 1:
        result["hint"] = "כמה רמות גימור עם שווי שימוש שונה — לשאול את העובד איזו, ואם לא יודע: ask_rep עם about=current_vehicle"
    return result


def set_current_situation(
    record: ConversationRecord,
    store: Store,
    kind: str,
    car_id: str | None = None,
    vou_key: str | None = None,
    participation: float | None = None,
    vehicle_description: str | None = None,
) -> dict:
    tracked_participation = _tracked(participation) if participation is not None else Tracked(source="missing")
    if kind == "waiver":
        option = OptionRequest(kind="waiver")
    elif car_id:
        if car_id not in _catalog():
            raise ToolError(f"רכב לא מוכר בקטלוג: {car_id}. רכב שלא בקטלוג — לחפש עם find_vehicle")
        option = OptionRequest(kind="car", car_id=car_id, participation=tracked_participation)
    elif vou_key:
        try:
            key = vou.parse_key(vou_key)
        except ValueError as e:
            raise ToolError(str(e))
        row = vou.lookup(key)
        if row is None:
            raise ToolError(f"המפתח לא נמצא במאגר: {vou_key}. לחפש עם find_vehicle")
        option = OptionRequest(kind="car", vou_key=key, vehicle_label=vehicle_label(row), participation=tracked_participation)
    elif vehicle_description:
        # הרכב עוד לא זוהה חד-משמעית: שומרים מה שהעובד אמר (ואת ההשתתפות), והנציג יזהה
        option = OptionRequest(kind="car", vehicle_label=f"{vehicle_description.strip()[:60]} (טרם זוהה)", participation=tracked_participation)
    else:
        raise ToolError("לרכב צריך car_id (מהקטלוג), vou_key (מ-find_vehicle), או vehicle_description אם עוד לא זוהה")
    record.state.profile.current_option = _tracked(option)
    return {"saved": option.model_dump(mode="json", exclude_none=True), "still_missing": missing_profile_fields(record)}


def describe_option(option: OptionRequest | None) -> str:
    """שם קצר לחלופה — לנציג, לפרופיל ולפרומפט."""
    if option is None:
        return "לא ידוע"
    if option.kind == "waiver":
        return "ויתור (אחזקת רכב)"
    if option.car_id:
        car = _catalog().get(option.car_id)
        return f"{car['model']} ({car['registration_year']})" if car else option.car_id
    return option.vehicle_label or "רכב מהמאגר"


def list_catalog(record: ConversationRecord, store: Store) -> dict:
    rules = load_rules()
    profile = record.state.profile
    occupation = _occupations().get(profile.occupation.value) if profile.occupation.value else None
    group = occupation["group"] if occupation else profile.group.value
    children = profile.children_under_23.value
    cars = []
    for c in seed.catalog():
        elig = check_car(c, group, children, rules)
        cars.append({
            "car_id": c["id"],
            "name": f"{c['manufacturer']} {c['model']} ({c['registration_year']})",
            "drive": DRIVE_LABELS[c["drive"]],
            "group": c["group"],
            "eligible": None if elig.eligibility is None else elig.eligibility.eligible,
        })
    return {"employee_group": group, "cars": cars}


def compare_options(record: ConversationRecord, store: Store, options: list[dict]) -> dict:
    current = record.state.profile.current_option.value
    alternatives = []
    for o in options:
        if o.get("kind") == "car":
            car_id = o.get("car_id")
            if car_id not in _catalog():
                raise ToolError(f"רכב לא מוכר בקטלוג: {car_id}")
            if current and current.kind == "car" and current.car_id == car_id:
                continue  # המצב היום הוא נקודת ההשוואה, לא חלופה
            if o.get("participation") is not None:
                record.participations[car_id] = _tracked(float(o["participation"]))
            alternatives.append(OptionRequest(kind="car", car_id=car_id))
        elif not (current and current.kind == "waiver"):
            alternatives.append(OptionRequest(kind="waiver"))
    record.alternatives = alternatives
    resp = run_comparison(record)
    return summarize(resp)


def ask_rep(
    record: ConversationRecord, store: Store, question_text: str, car_id: str | None = None, about: str | None = None
) -> dict:
    if about == "current_vehicle":
        field = "current_vehicle"
    elif car_id:
        field = f"participation:{car_id}"
    else:
        field = "other"
    for q in store.questions(conversation_id=record.state.conversation_id, status="open"):
        if q.field == field:
            return {"already_open": True, "question_id": q.id}
    catalog = _catalog()
    profile = record.state.profile
    current_text = describe_option(profile.current_option.value)
    group = profile.group.value
    if profile.occupation.value:
        group = _occupations()[profile.occupation.value]["group"]
    context = f"קבוצה {group or '?'} · היום: {current_text}"
    if car_id in catalog and not any(a.car_id == car_id for a in record.alternatives):
        record.alternatives.append(OptionRequest(kind="car", car_id=car_id))
    meanwhile = run_comparison(record) if record.alternatives else None
    if car_id in catalog:
        context += f" · מבוקש: {catalog[car_id]['model']} ({catalog[car_id]['registration_year']})"
        asked = next((a for a in meanwhile.alternatives if a.option.car_id == car_id), None) if meanwhile else None
        if asked and asked.break_even_participation is not None and asked.break_even_participation > 0:
            context += f" · משתלם לעובד עד השתתפות של {math.floor(asked.break_even_participation):,} ₪"
    q = RepQuestion(
        id=uuid4().hex[:10],
        conversation_id=record.state.conversation_id,
        employee_display_name=record.state.employee_display_name or "עובד/ת",
        field=field,
        question_text=question_text,
        context=context,
        status="open",
        created_at=datetime.now(timezone.utc),
    )
    store.save_question(q)
    insights.record_rep_question(store, record.state.conversation_id, field)
    record.state.status = "waiting_for_rep"
    result = {"sent": True, "question_id": q.id}
    # בינתיים: השוואה חלקית — המצב היום מחושב, והחלופה תתעדכן כשהנציג יענה
    if meanwhile:
        result["comparison_meanwhile"] = summarize(meanwhile)
    return result


TOOLS = {
    "update_profile": update_profile,
    "set_current_situation": set_current_situation,
    "find_vehicle": find_vehicle,
    "list_catalog": list_catalog,
    "compare_options": compare_options,
    "ask_rep": ask_rep,
}


# ---------- עזר משותף לסוכן ולמענה הנציג ----------


def run_comparison(record: ConversationRecord) -> CompareResponse:
    """מריץ את ההשוואה האחרונה מחדש עם כל מה שידוע עכשיו (למשל אחרי תשובת נציג)."""
    alternatives = []
    for alt in record.alternatives:
        if alt.kind == "car":
            known = record.participations.get(alt.car_id)
            alternatives.append(OptionRequest(kind="car", car_id=alt.car_id, participation=known or Tracked(source="missing")))
        else:
            alternatives.append(alt)
    resp = compare(CompareRequest(profile=record.state.profile, alternatives=alternatives))
    record.state.last_comparison = resp
    return resp


def summarize(resp: CompareResponse) -> dict:
    """מה שהמודל רואה מההשוואה — מספרים מעוגלים לשקל, כמו בתצוגה."""

    def one(r, is_current=False):
        out = {"label": r.label, "status": r.status, "explanation": r.explanation}
        if r.net is not None:
            out["net"] = round(r.net)
        if not is_current:
            out["eligible"] = r.eligibility.eligible
            if r.delta_display is not None:
                out["delta"] = r.delta_display
            if r.break_even_participation is not None:
                # עד כמה ההשתתפות יכולה להגיע בלי שהנטו ירד מתחת להיום (מעוגל כלפי מטה, לצד הבטוח)
                out["break_even_participation"] = math.floor(r.break_even_participation)
        if r.missing:
            out["missing"] = r.missing
        return out

    return {"today": one(resp.current, True), "alternatives": [one(a) for a in resp.alternatives]}


def missing_profile_fields(record: ConversationRecord) -> list[str]:
    p = record.state.profile
    missing = [f for f in PROFILE_NUMBERS if p.__getattribute__(f).value is None]
    if p.occupation.value is None and p.group.value is None:
        missing.insert(0, "occupation")
    current = p.current_option.value
    if current is None:
        missing.append("current_situation")
    elif current.kind == "car":
        if current.car_id is None and current.vou_key is None:
            missing.append("current_vehicle")
        if current.participation is None or current.participation.value is None:
            missing.append("current_participation")
    return missing
