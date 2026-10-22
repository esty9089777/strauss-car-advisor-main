# יועץ הרכב של שטראוס

איג'נט בעברית שעונה לעובד: "מה יקרה לנטו שלי אם אחליף רכב, אעבור לחשמלי או אוותר על הרכב?"

## עקרונות

1. **החישוב בקוד, לא במודל.** Gemini מחלץ נתונים, בוחר כלים ומנסח. כל מספר מגיע מהמנוע.
2. **לא מנחשים.** לכל נתון בפרופיל יש מקור (`employee` / `payslip` / `rep` / `catalog` / `rules` / `missing`). על נתון חסר המנוע לא רץ. במקום זה שואלים את העובד או שולחים שאלה לנציג.
3. **Number Guard.** כל מספר בתשובת הבוט חייב להופיע ב-trace של החישוב. אחרת התשובה נחסמת.
4. **כללים כנתונים.** `config/strauss-rules.json` עם ולידציה. בפיילוט מחליפים את הקובץ, לא את הקוד.

## הפעלה

```bash
# שרת (Python 3.12)
cd backend
pip install -r requirements.txt
cp .env.example .env          # רק אם .env עדיין לא קיים
# מגדירים ADMIN_USERNAME ככתובת דוא״ל ו-ADMIN_PASSWORD באורך 12 תווים לפחות
# ממלאים GEMINI_API_KEY לשיחה; לתלושים אמיתיים מגדירים Vertex AI
python -m uvicorn app.main:app --port 8000   # אחרי שינוי בקוד: לעצור ולהפעיל שוב (--reload נתקע אצלנו ב-Windows)
python -m pytest -q            # בדיקות

# ממשק (Node 20+), בטרמינל נוסף
cd frontend
npm install
npm run dev                    # http://localhost:5173
```

חשבונות העובדים, הנציגים והשיחות הפרטיות נשמרים ב-`data/app.db`. עובדים נרשמים לבד ב-`/employee/register` (שם, דוא״ל וסיסמה) ונכנסים ב-`/login`. **נציגים נרשמים רק בהזמנה**, כי הם רואים את השאלות של כל העובדים: מנהל/ת נכנס/ת דרך `/admin/login` עם `ADMIN_USERNAME` (כתובת דוא״ל) ו-`ADMIN_PASSWORD` באורך 12 תווים לפחות, ובמסך "ניהול נציגים" מנפיק/ה קוד בן 5 ספרות, הקשור לדוא״ל ותקף ל-24 שעות. הנציג/ה נרשם/ת ב-`/rep/register` ונכנס/ת ב-`/rep/login`. להדגמה ולשופטות: `DEMO_REP_EMAIL` ו-`DEMO_REP_PASSWORD` יוצרים חשבון נציג/ה קבוע בכל עלייה של השרת, גם אחרי פריסה שמחקה את הנתונים. במסך `/insights` ("דוח תובנות") המנהל/ת רואה את נקודות הבלבול של העובדים, בלי פרטים אישיים, ובתחילת כל חודש נשמר שם הדוח של החודש הקודם. כללי המערכת נמצאים במסך הנפרד `/rules`, ושמירתם דורשת התחברות מנהל/ת. שאלות פתוחות משותפות לצוות, ורק הנציג/ה שענה/תה רואה את השאלה בהיסטוריית המענה שלו/ה. קוד `REP_ACCESS_CODE` נשאר לחיפוש כלי רכב.

במסך העובד ניתן להעלות תלוש PDF או תמונה עד 10 MB. הקובץ נשלח לשירות ה-AI שהוגדר לצורך חילוץ ואינו נשמר ביישום. הערכים שחולצו מוצגים לבדיקה ולתיקון; רק לאחר אישור העובד הם נשמרים בפרופיל. לצורך תלושים אמיתיים יש להשתמש ב-Vertex AI, או להפעיל במפורש `PAYSLIP_ALLOW_GEMINI_API=true` רק לאחר אישור תוכנית Gemini בתשלום. חילוץ דורש גם הגדרת `GEMINI_API_KEY` או Vertex AI.

> **אזהרת פיתוח בלבד:** בלי `ADMIN_USERNAME` (כתובת דוא״ל תקינה) ו-`ADMIN_PASSWORD` באורך 12 תווים לפחות, כניסת מנהל/ת תחזיר הודעת הגדרה חסרה. אחרי שינוי `.env` יש להפעיל מחדש את השרת. אם `REP_ACCESS_CODE` חסר או ריק ב-`.env`, חיפוש כלי הרכב עשוי להיות נגיש ללא קוד. יש להגדיר את הפרטים לפני כל שימוש מחוץ לפיתוח מקומי; ראו `docs/PRIVACY.md`.

## העלאה לאוויר (Render)

| שירות | הגדרות |
|---|---|
| **שרת** (Web Service) | Root Directory `backend` · Build `pip install -r requirements.txt` · Start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. משתני סביבה: `GEMINI_API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `DEMO_REP_EMAIL`, `DEMO_REP_PASSWORD`, `REP_ACCESS_CODE`, `CORS_ORIGINS` = כתובת הממשק |
| **ממשק** (Static Site) | Root Directory `frontend` · Build `npm install && npm run build` · Publish `dist`. משתנה סביבה: `VITE_API_BASE_URL` = כתובת השרת |

- שני השירותים בונים מהענף `main`. Auto-Deploy פועל, כך שכל מיזוג ל-`main` עולה לאוויר.
- **keep-alive:** השרת החינמי של Render נרדם אחרי 15 דקות בלי בקשות. לכן השרת פונה לעצמו כל 10 דקות (`/api/health`), לפי `RENDER_EXTERNAL_URL` ש-Render מגדיר לבד. מקומית זה לא פועל.
- **הנתונים נשמרים ב-SQLite על הדיסק של Render, והדיסק הזה זמני.** ה-keep-alive מונע מחיקה בגלל הירדמות, אבל חשבונות, שיחות ושאלות לנציג עדיין נמחקים בכל פריסה, כלומר בכל מיזוג ל-`main`. לפני הדמו לא ממזגים, או יוצרים מחדש את חשבונות הדמו אחרי הפריסה.
- התוכנית החינמית נותנת 750 שעות שרת בחודש לכל החשבון. שרת אחד שרץ כל הזמן צורך כ-744 שעות, כך שאין מקום לשרת חינמי נוסף שגם הוא ער כל הזמן.
- `VITE_API_BASE_URL` נקרא בזמן הבנייה. אחרי שינוי שלו צריך לבנות את הממשק מחדש.
- שינויי כללים ממסך `/rules` נשמרים בקובץ על השרת, ולכן ב-Render הם חוזרים לגרסה שב-git בכל הפעלה מחדש. שינוי קבוע: עורכים את `config/strauss-rules.json` ב-git.

## מבנה

```
config/strauss-rules.json   כללי האתגר (תקציבים, מסלולים, שיטת הנטו)
data/raw/                   מאגר שווי השימוש (cp1255) + אקסל האתגר
data/seed/                  JSON שיוצא מהאקסל: catalog, occupations, employees, golden
backend/app/contracts.py    החוזים: מקור האמת למבני הנתונים
backend/app/engine/         המנוע: rules (כללים) · vou (מאגר שווי שימוש) · eligibility · net · compare
backend/app/agent/          הסוכן: orchestrator (לולאת Gemini) · tools · number_guard · prompts · rep_flow
backend/app/store.py        חשבונות, sessions, שיחות ושאלות לנציג ב-SQLite (data/app.db)
backend/app/api/            נתיבי ה-API (ראו docs/CONTRACTS.md)
backend/scripts/            export_seed.py: אקסל → seed
frontend/src/design/        מערכת העיצוב המשותפת (tokens + רכיבים), דוגמאות ב-/design
frontend/src/pages/         עובד · נציג · כללים · מערכת עיצוב
docs/                       חוזים, ממצאי נתונים, תוכנית עבודה
```

## בעלות

| | מפתחת א': המספרים והשקיפות | מפתחת ב': השיחה והאנשים |
|---|---|---|
| לוגיקה | `engine/` (נטו + trace), טעינת המאגר, תור הנציג | `rules`, זכאות, `agent/` (Gemini + כלים), Number Guard |
| API | `/api/compare`, `/api/rep/*` | `/api/chat`, `/api/conversations`, `/api/rules` |
| מסכים | השוואה + גרף מפל, מסך נציג | צ'אט + כרטיס פרופיל, מסך כללים |

כל Pull Request עובר review של השנייה.
