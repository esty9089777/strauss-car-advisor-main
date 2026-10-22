# חוזים בין השכבות

מקור האמת הוא `backend/app/contracts.py`. המסמך הזה מסביר אותו. משנים חוזה רק בהסכמה של שתיכן, ומריצים `pytest` אחרי כל שינוי.

## נתיבים

| נתיב | מצב | בעלות | מחזיר |
|---|---|---|---|
| `GET /api/catalog` | אמיתי | משותף | `list[Car]` |
| `GET /api/occupations` | אמיתי | משותף | `list[Occupation]` |
| `GET /api/demo-employees` | אמיתי | משותף | 6 העובדים לדוגמה, בלי ת״ז |
| `GET /api/rules` | אמיתי | ב' | קובץ הכללים |
| `POST /api/rules/preview` | אמיתי | ב' | `{valid, version, impact}` ← ולידציה + ההשפעה על 6 העובדים לדוגמה (לפני/אחרי). לא שומר. דורש session של מנהל/ת. על כללים לא תקינים מוחזר 422 עם `detail: {message, problems}` |
| `PUT /api/rules` | אמיתי | ב' | שומר אחרי ולידציה; הגרסה הקודמת נשמרת ב-`config/history/`. דורש session של מנהל/ת |
| `POST /api/rules/undo` | אמיתי | ב' | משחזר את הגרסה האחרונה מההיסטוריה (404 אם אין); דורש session של מנהל/ת |
| `POST /api/auth/login` | אמיתי | משותף | כניסה עם דוא״ל וסיסמה; מחזיר session עם role |
| `POST /api/auth/employees/register` | אמיתי | הרשמה ציבורית | עובד/ת: שם מלא, דוא״ל וסיסמה (טלפון לא חובה) → חשבון ו-session |
| `POST /api/auth/invitations` | אמיתי | מנהל/ת | הזמנת נציג/ה: קוד בן 5 ספרות, קשור לדוא״ל, תקף ל-24 שעות. הזמנה חוזרת מחליפה את הקוד |
| `POST /api/auth/representatives/register` | אמיתי | הרשמה בהזמנה | נציג/ה: דוא״ל + קוד + סיסמה → חשבון ו-session. קוד חד-פעמי, עד 10 ניסיונות ב-15 דקות |
| `GET /api/admin/insights`, `GET /api/admin/insights/reports[/{month}]` | אמיתי | מנהל/ת | דוח נקודות הבלבול של החודש (או דוח חודשי שמור): ספירות לפי נושא, `min_count` = סף הצגה. נושא מפחות מ-3 שיחות מוסתר |
| `GET /api/auth/me`, `POST /api/auth/logout` | אמיתי | session | פרטי המשתמש המחובר/ת וביטול session |
| `POST /api/conversations/{id}/explore` | אמיתי | א' | כל החלופות בבת אחת, בלי מודל שפה. 409 אם הפרופיל לא שלם |
| `GET /api/vehicles/search?q=&year=` | אמיתי, **קוד נציג** | א' | חיפוש במאגר המלא — לזיהוי הרכב של היום |
| `GET /api/auth/check/{rep\|admin}` | אמיתי | משותף | 200 אם הקוד בכותרת `X-Access-Code` פותח את המסך |

**הרשאות:** ניהול נציגים, הנפקת קודי עובד/ת וכללי המערכת דורשים session של מנהל/ת. קוד עובד/ת חד-פעמי בן 5 ספרות נשמר כ-hash, קשור לדוא״ל ופג אחרי 24 שעות; קוד חדש מבטל קודם. קוד נציג/ה אקראי פג אחרי 24 שעות ונמחק לאחר שימוש. הרשמת עובד/ת עצמאית יוצרת role של עובד/ת בלבד ואינה מקבלת role מהלקוח. חילוץ תלוש ואישור שדות דורשים session עובד/ת והסכמה לשליחת המסמך. `/api/rep/*` ו-`/api/vehicles/search` ממשיכים להשתמש בהרשאות הנציג הקיימות.

**רכב מחוץ לקטלוג:** `OptionRequest` עם `vou_key` (ובלי `car_id`) — רק כמצב היום. כחלופה הוא `blocked` עם `not_in_catalog`. רכב בלי שניהם = "טרם זוהה": המצב היום `partial` עם `missing=["current_vehicle"]`, ושאלה לנציג מסוג `current_vehicle` (התשובה: מפתח מהמאגר, לא טקסט חופשי).
| `POST /api/compare` | אמיתי | א' | `CompareResponse` (400 על רכב לא מוכר) |
| `POST /api/compare/all` | אמיתי | א' | כל רכב בקטלוג + ויתור, ממוין: מחושבות לפי ההפרש, אחריהן חלקיות, בסוף לא זמינות |
| `GET /api/demo/compare/{id}` | אמיתי | משותף | `compare/all` לאחד מ-6 העובדים לדוגמה |
| `POST /api/chat` | אמיתי (Gemini + כלים) | ב' | `ConversationState` |
| `POST /api/payslip/extract` | אמיתי (מודל מולטימודלי) | עובד/ת | קובץ PDF/תמונה עד 10 MB; דורש אישור מפורש. מחזיר שדות לבדיקה, לא שומר את הקובץ או את השדות |
| `POST /api/conversations/{id}/payslip/confirm` | אמיתי | עובד/ת | שומר רק שדות שאושרו, עם `source="payslip"`; מחשב מחדש אם קיימות חלופות |
| `GET /api/conversations/{id}` | אמיתי (SQLite) | ב' | `ConversationState`, כולל `pending_questions` |
| `POST /api/conversations/{id\|new}/demo/{employee_id}` | אמיתי | ב' | ממלא את הפרופיל מעובד לדוגמה, בלי מודל |
| `GET /api/rep/questions?status=open` | אמיתי (SQLite) | א' | `list[RepQuestion]` |
| `POST /api/rep/questions/{id}/answer` | אמיתי | א' | `RepQuestion`. 409 אם כבר נענתה, 400 על השתתפות שלילית |

## מושגים מרכזיים

**`Tracked[T]`**: ערך ומקור. `source="missing"` אומר שהערך לא ידוע. המנוע מחזיר `status="partial"` ומוסיף את השדה ל-`missing`. הוא אף פעם לא ממלא ערך בעצמו.

**`OptionResult.status`**
- `complete`: יש את כל הנתונים, כל המספרים מחושבים.
- `partial`: חסר נתון (בדרך כלל השתתפות). נשלחה שאלה לנציג. `net` ו-`delta_net` הם `null`.
- `blocked`: אין זכאות. `eligibility.reason_text` מסביר למה.

**`trace`**: הקבלה של החישוב. ה-Number Guard בודק את הטקסט של הבוט מול השדות `amount` שבה.

**`waterfall`**: פירוק ההפרש. סכום ה-`amount` שווה בדיוק ל-`delta_net`. יש לזה בדיקה.

**`delta_net` מול `delta_display`**: `delta_net` הוא ההפרש המדויק. `delta_display` הוא נטו מעוגל פחות נטו מעוגל, וזה מה **שמציגים** (בכרטיס ובטקסט של הבוט). אחרת העובד יכול לראות 15,047 ← 13,109 ולצידם "−1,939". גם גיליון הייחוס מחשב כך.

**השתתפות ברכב חדש**:
- `participation` לא נשלח (None) ← ברירת המחדל מהקטלוג, ומסומנת ב-trace כ"קטלוג (ברירת מחדל)".
- `participation` נשלח עם `source="missing"` ← החלופה `partial`, ושואלים את הנציג.
הסוכן מחליט איזה מהשניים לפי מה שהעובד אמר.

**קבוצת הזכאות**: כשהעיסוק ידוע, הקבוצה נקבעת לפי טבלת העיסוקים ולא לפי מה שהעובד הקליד.

**עיגול**: השרת מחזיר ערכים בלי עיגול. הממשק מעגל לשקל ב-`design/format.js`.

## הזרימה כשחסר נתון

1. הסוכן מזהה שחסרה השתתפות לרכב X. הוא יוצר `RepQuestion` עם `field="participation:CAR-XX"`, וה-`status` של השיחה הופך ל-`waiting_for_rep`.
2. העובד רואה השוואה חלקית (`partial`) עם ההודעה "שאלתי את הנציג".
3. הנציג עונה. הערך נכנס לפרופיל עם `source="rep"` ו-`note` (תאריך תחולה), ומתבצע חישוב מחדש.
4. הממשק של העובד מושך את `GET /api/conversations/{id}` כל 3 שניות ורואה את ההשוואה המעודכנת.
