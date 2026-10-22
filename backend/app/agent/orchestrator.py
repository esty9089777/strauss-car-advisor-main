"""לולאת הסוכן: Gemini בוחר כלים, הקוד מריץ אותם, ו-Number Guard בודק כל תשובה לפני שהיא נשלחת.

את הלולאה מריצים אנחנו (automatic function calling כבוי) — כדי שאף טקסט לא יגיע לעובד בלי בדיקה.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app import insights
from app.agent import number_guard
from app.agent.prompts import system_prompt
from app.agent.tools import TOOL_SPECS, TOOLS, ToolError, describe_option, missing_profile_fields
from app.contracts import ChatMessage, CompareResponse
from app.engine.rules import load_rules
from app.store import ConversationRecord, Store

load_dotenv()
log = logging.getLogger(__name__)

MAX_STEPS = 8
FALLBACK_TEXT = "הכנתי את ההשוואה — כל המספרים מופיעים בכרטיסים ליד הצ'אט, עם הסבר לכל חלופה."
ERROR_TEXT = "סליחה, יש לי תקלה זמנית בחיבור. אפשר לנסות שוב בעוד רגע — שום נתון לא אבד."


class LLM(Protocol):
    def generate(self, history: list[types.Content], system: str) -> types.Content: ...


class GeminiLLM:
    """מודל ראשי + מודל גיבוי: בעומס (503) או חריגה ממכסה (429) עוברים לגיבוי, ואם גם הוא נכשל — ממתינים פעם אחת."""

    RETRYABLE = (429, 503)

    def __init__(self):
        # פיילוט עם נתונים אמיתיים: Vertex AI (הנתונים לא משמשים לאימון). מוגדר ב-.env בלבד — ראו docs/PRIVACY.md
        if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"):
            self.client = genai.Client()  # קורא GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION מהסביבה
        else:
            self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.models = [
            m for m in (os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"), os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")) if m
        ]
        self.tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(name=s["name"], description=s["description"], parameters_json_schema=s["parameters"])
                    for s in TOOL_SPECS
                ]
            )
        ]

    def generate(self, history, system):
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=self.tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )
        attempts = self.models + self.models[:1]  # הסבב האחרון: שוב הראשי, אחרי המתנה
        for i, model in enumerate(attempts):
            if i == len(self.models):
                time.sleep(8)
            try:
                resp = self.client.models.generate_content(model=model, contents=history, config=config)
                content = resp.candidates[0].content if resp.candidates else None
                return content or types.Content(role="model", parts=[types.Part(text="")])
            except genai_errors.APIError as e:
                if e.code not in self.RETRYABLE or i == len(attempts) - 1:
                    raise
                log.warning("Gemini %s on %s, trying next", e.code, model)


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = GeminiLLM()
    return _llm


def use_llm(llm: LLM) -> None:
    """לבדיקות: מודל מתוסרט במקום Gemini."""
    global _llm
    _llm = llm


def _message(role: str, text: str, comparison: CompareResponse | None = None) -> ChatMessage:
    return ChatMessage(id=uuid4().hex, role=role, text=text, created_at=datetime.now(timezone.utc), comparison=comparison)


def _execute(name: str, args: dict, record: ConversationRecord, store: Store) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"כלי לא קיים: {name}"}
    try:
        return tool(record, store, **args)
    except ToolError as e:
        return {"error": str(e)}
    except (TypeError, ValueError) as e:
        return {"error": f"קלט לא תקין לכלי {name}: {e}"}


def profile_summary(record: ConversationRecord, store: Store) -> str:
    """מצב הפרופיל העדכני — נכנס לפרומפט בכל תור, כדי שהמודל לא יסתמך על הזיכרון שלו."""
    p = record.state.profile
    known = {
        name: getattr(p, name).value
        for name in ("occupation", "group", "base_salary", "commissions", "bonus", "pension_pct", "study_fund_pct", "credit_points", "children_under_23")
        if getattr(p, name).value is not None
    }
    current = p.current_option.value
    if current is not None:
        known["current_situation"] = describe_option(current)
        if current.kind != "waiver":
            known["current_participation"] = current.participation.value if current.participation else None
    participations = {car: t.value for car, t in record.participations.items()}
    open_questions = [q.question_text for q in store.questions(conversation_id=record.state.conversation_id, status="open")]
    return (
        "\n## מצב הפרופיל עכשיו (מקור אמת — עדיף על מה שזכור מהשיחה)\n"
        f"- ידוע: {json.dumps(known, ensure_ascii=False)}\n"
        f"- עדיין חסר: {', '.join(missing_profile_fields(record)) or 'כלום — אפשר להשוות'}\n"
        f"- השתתפות ידועה ברכבים חדשים: {json.dumps(participations, ensure_ascii=False)}\n"
        f"- שאלות פתוחות לנציג: {open_questions or 'אין'}\n"
    )


def handle_message(record: ConversationRecord, text: str, store: Store) -> ConversationRecord:
    rules = load_rules()
    system = system_prompt(rules) + profile_summary(record, store)
    is_first = not any(m.role == "user" for m in record.state.messages)
    record.topics_asked = insights.record_message(store, record.state.conversation_id, text, record.topics_asked, is_first)
    record.state.messages.append(_message("user", text))

    saved_history = list(record.history)
    history = [types.Content.model_validate(h) for h in record.history]
    history.append(types.Content(role="user", parts=[types.Part(text=text)]))

    comparisons: list[CompareResponse] = []
    retried = False
    reply = None
    try:
        for _ in range(MAX_STEPS):
            content = get_llm().generate(history, system)
            history.append(content)
            calls = [p.function_call for p in content.parts or [] if p.function_call]
            if calls:
                parts = []
                for call in calls:
                    result = _execute(call.name, dict(call.args or {}), record, store)
                    if (call.name == "compare_options" and "error" not in result) or "comparison_meanwhile" in result:
                        comparisons.append(record.state.last_comparison)
                    parts.append(types.Part.from_function_response(name=call.name, response=result))
                history.append(types.Content(role="user", parts=parts))
                continue

            reply = "".join(p.text for p in content.parts or [] if p.text and not p.thought).strip()
            known = comparisons + ([record.state.last_comparison] if record.state.last_comparison else [])
            turn_history = [c.model_dump(mode="json", exclude_none=True) for c in history]  # כולל תוצאות הכלים מהתור הזה
            unbacked = number_guard.check(reply, number_guard.allowed_numbers(record, rules, known, turn_history))
            if not unbacked:
                break
            record.guard_events.append({"at": datetime.now(timezone.utc).isoformat(), "text": reply, "unbacked": unbacked})
            insights.record_guard_block(store, record.state.conversation_id, insights.topics_in(text))
            log.warning("Number Guard blocked %s", unbacked)
            if retried:
                reply = FALLBACK_TEXT
                break
            retried = True
            numbers = ", ".join(f"{n:g}" for n in unbacked)
            history.append(types.Content(role="user", parts=[types.Part(
                text=f"[בקרת מספרים — הודעת מערכת, לא מהעובד] המספרים {numbers} לא הגיעו מאף כלי. "
                     "כתוב/כתבי את התשובה מחדש, רק עם מספרים מתוצאות הכלים."
            )]))
        else:
            reply = reply or FALLBACK_TEXT
    except Exception:  # תקלת רשת / מכסה — לא מאבדים את השיחה
        log.exception("LLM call failed")
        record.history = saved_history
        record.state.messages.append(_message("assistant", ERROR_TEXT))
        return record

    record.history = [c.model_dump(mode="json", exclude_none=True) for c in history]
    record.state.messages.append(_message("assistant", reply or FALLBACK_TEXT, comparisons[-1] if comparisons else None))
    update_status(record, store)
    return record


def update_status(record: ConversationRecord, store: Store) -> None:
    if store.questions(conversation_id=record.state.conversation_id, status="open"):
        record.state.status = "waiting_for_rep"
    elif record.state.last_comparison is not None:
        record.state.status = "ready"
    else:
        record.state.status = "collecting"
