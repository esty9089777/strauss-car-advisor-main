import { useEffect, useState } from "react";

import { api } from "../api";
import { Button, Card, Input, Notice, Stat } from "../design/components";

const LIVE = "live";
const num = new Intl.NumberFormat("he-IL", { maximumFractionDigits: 1 });

function monthLabel(month) {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("he-IL", { month: "long", year: "numeric" });
}

/** נושא אחד: פס באורך יחסי לציון הבלבול (סדרה אחת, צבע אחד), המספרים, ופעולה מוצעת */
function TopicRow({ topic, max, rank }) {
  const width = max ? Math.max(4, (topic.score / max) * 100) : 0;
  const detail = `${topic.asked} שאלות · ${topic.repeats} שאלות חוזרות · ${topic.rep} לנציג · ${topic.conversations} שיחות`;
  return (
    <li className="py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="font-medium">
          <span className="ms-0.5 text-ink-soft">{rank}.</span> {topic.label}
        </div>
        <div className="num text-xs text-ink-soft">{detail}</div>
      </div>
      <div className="mt-2 h-2.5 rounded-full bg-muted" title={detail}>
        <div className="h-full rounded-full bg-brand" style={{ width: `${width}%` }} />
      </div>
      <p className="mt-2 text-sm text-ink-soft">💡 {topic.action}</p>
    </li>
  );
}

export default function InsightsPage() {
  const [months, setMonths] = useState([]);
  const [selected, setSelected] = useState(LIVE);
  const [minCount, setMinCount] = useState(3);
  const [minutes, setMinutes] = useState("");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function load(which = selected, count = minCount) {
    setLoading(true);
    setError("");
    try {
      setReport(which === LIVE ? await api.insights(count) : await api.insightReport(which, count));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    api.insightReports().then(setMonths).catch(() => {});
    load(LIVE);
  }, []);

  const resolvedPct = report?.conversations ? Math.round((report.resolved_without_rep / report.conversations) * 100) : null;
  const hoursSaved = report && Number(minutes) > 0 ? (report.resolved_without_rep * Number(minutes)) / 60 : null;
  const maxScore = Math.max(0, ...(report?.topics ?? []).map((t) => t.score));

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <section>
        <h1 className="font-display text-3xl font-bold">דוח נקודות הבלבול</h1>
        <p className="mt-2 text-ink-soft">
          על מה עובדים שואלים שוב ושוב, ואיפה הם נתקעים, כדי לשפר את ההסברים והתהליך במקום לענות על אותן שאלות.
          בתחילת כל חודש נשמר כאן לבד הדוח של החודש שהסתיים.
        </p>
      </section>

      <Card className="glass print:shadow-none">
        <div className="flex flex-wrap items-end gap-3 print:hidden">
          <label className="block min-w-[12rem] flex-1">
            <span className="mb-1.5 block text-sm font-medium">דוח</span>
            <select
              className="w-full rounded-2xl border border-border bg-surface px-4 py-2.5 text-sm outline-none focus:border-brand"
              value={selected}
              onChange={(e) => {
                setSelected(e.target.value);
                load(e.target.value);
              }}
            >
              <option value={LIVE}>החודש הנוכחי (מתעדכן)</option>
              {months.map((m) => (
                <option key={m.month} value={m.month}>{monthLabel(m.month)}</option>
              ))}
            </select>
          </label>
          <div className="w-40">
            <Input
              label="להציג נושא מ-X פעמים"
              type="number"
              min={1}
              max={1000}
              value={minCount}
              onChange={(e) => setMinCount(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
          <Button onClick={() => load()} disabled={loading}>{loading ? "מפיק…" : "הפקת דוח עכשיו"}</Button>
          <Button variant="secondary" onClick={() => window.print()} disabled={!report}>הדפסה / PDF</Button>
        </div>

        {error && <p role="alert" className="mt-4 text-sm text-down">{error}</p>}

        {report && (
          <>
            <div className="mt-6 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="font-display text-xl font-bold">{monthLabel(report.month)}</h2>
              <span className="text-xs text-ink-soft">
                הופק {new Date(report.generated_at).toLocaleString("he-IL")}
              </span>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-5 sm:grid-cols-4">
              <Stat label="שיחות">{num.format(report.conversations)}</Stat>
              <Stat label="נפתרו בלי נציג" sub={resolvedPct != null ? `${resolvedPct}% מהשיחות` : undefined}>
                {num.format(report.resolved_without_rep)}
              </Stat>
              <Stat label="שאלות לנציג">{num.format(report.rep_questions)}</Stat>
              <Stat label="תשובות שנוסחו מחדש" sub="נחסמו בבקרת המספרים">{num.format(report.guard_blocks)}</Stat>
            </div>

            <div className="mt-5 flex flex-wrap items-end gap-3 rounded-2xl bg-up-soft px-4 py-3">
              <div className="w-48 print:hidden">
                <Input
                  label="דקות לבירור ידני אחד"
                  type="number"
                  min={1}
                  placeholder="למשל 30"
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value)}
                />
              </div>
              <p className="flex-1 pb-2 text-sm">
                {hoursSaved != null ? (
                  <>
                    ⏱️ <span className="font-bold">חיסכון של כ-{num.format(hoursSaved)} שעות עבודה</span>: {report.resolved_without_rep} שיחות
                    שנפתרו בלי נציג × {minutes} דקות לבירור.
                  </>
                ) : (
                  "⏱️ כמה זמן לוקח היום לברר שאלה כזו במיילים? הזינו, ונחשב את שעות העבודה שנחסכו. המערכת לא מנחשת את הנתון הזה."
                )}
              </p>
            </div>

            <h3 className="mt-8 font-display text-lg font-bold">נקודות הבלבול, מהמבלבלת ביותר</h3>
            {report.topics.length ? (
              <ol className="mt-1 divide-y divide-border/70">
                {report.topics.map((t, i) => <TopicRow key={t.topic} topic={t} max={maxScore} rank={i + 1} />)}
              </ol>
            ) : (
              <p className="mt-3 text-sm text-ink-soft">
                עוד אין נושא שעלה לפחות {report.min_count} פעמים ב-{report.min_conversations} שיחות שונות לפחות. הדוח
                מתמלא ככל שעובדים משתמשים ביועץ.
              </p>
            )}
            <p className="mt-4 text-xs text-ink-soft">
              הסדר לפי ציון בלבול: שאלה חוזרת באותה שיחה, שאלה שנשלחה לנציג ותשובה שנאלצנו לנסח מחדש שוקלות כפול משאלה
              רגילה.
            </p>
          </>
        )}
      </Card>

      <Notice tone="brand">
        🔒 פרטיות: מכל הודעה נשמרים רק הנושאים שלה מתוך רשימה קבועה. לא נשמרים טקסט, שמות או סכומים, והספירות
        לא קשורות לעובד/ת. נושא שעלה בפחות מ-{report?.min_conversations ?? 3} שיחות לא מוצג
        {report?.hidden_topics ? ` (${report.hidden_topics} נושאים הוסתרו החודש)` : ""}.
      </Notice>
    </div>
  );
}
