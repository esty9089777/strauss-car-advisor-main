"""שמירת שיחות ושאלות לנציג ב-SQLite (data/app.db) — שורד רענון וגם הפעלה מחדש של השרת.

כל רשומה נשמרת כ-JSON של מודל Pydantic. פשוט, ומספיק לפיילוט של עשרות שיחות במקביל.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.contracts import ConversationState, EmployeeProfile, OptionRequest, RepQuestion, Tracked
from app.seed import ROOT

DB_PATH = ROOT / "data" / "app.db"


class ConversationRecord(BaseModel):
    """מה שנשמר לכל שיחה. state הוא מה שהממשק רואה; השאר — זיכרון פנימי של הסוכן."""

    state: ConversationState
    history: list[dict] = Field(default_factory=list)  # ההיסטוריה של Gemini, כולל קריאות לכלים
    alternatives: list[OptionRequest] = Field(default_factory=list)  # החלופות בהשוואה האחרונה
    participations: dict[str, Tracked[float]] = Field(default_factory=dict)  # car_id → השתתפות ידועה
    guard_events: list[dict] = Field(default_factory=list)  # מה ש-Number Guard חסם
    topics_asked: list[str] = Field(default_factory=list)  # נושאים שכבר עלו בשיחה — לזיהוי שאלה חוזרת בדוח


class Store:
    def __init__(self, path: Path = DB_PATH):
        self._lock = threading.Lock()
        self._con = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._con.executescript(
            """
            PRAGMA journal_mode = WAL;      -- קוראים לא חוסמים כותבים
            PRAGMA busy_timeout = 5000;
            CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS employee_profiles (
                owner_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rep_questions (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'employee',
                full_name TEXT, mobile_number TEXT, password_setup_hash TEXT,
                password_setup_expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS employee_setup_attempts (
                email TEXT NOT NULL, client_ip TEXT NOT NULL,
                attempts INTEGER NOT NULL, window_started_at REAL NOT NULL,
                PRIMARY KEY (email, client_ip)
            );
            -- דוח נקודות הבלבול: רק נושא + גיבוב שיחה, בלי טקסט ובלי פרטים אישיים (ראו insights.py)
            CREATE TABLE IF NOT EXISTS topic_events (
                month TEXT NOT NULL, kind TEXT NOT NULL, topic TEXT NOT NULL,
                conversation_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_topic_events_month ON topic_events (month);
            CREATE TABLE IF NOT EXISTS monthly_reports (
                month TEXT PRIMARY KEY, counts TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_questions_status ON rep_questions (status, created_at);
            CREATE INDEX IF NOT EXISTS idx_questions_conversation ON rep_questions (conversation_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations (updated_at);
            """
        )
        columns = {row[1] for row in self._con.execute("PRAGMA table_info(conversations)")}
        if "owner_id" not in columns:
            self._con.execute("ALTER TABLE conversations ADD COLUMN owner_id TEXT")
        self._con.execute("CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations (owner_id)")
        user_columns = {row[1] for row in self._con.execute("PRAGMA table_info(users)")}
        if "role" not in user_columns:
            self._con.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'")
        if "full_name" not in user_columns:
            self._con.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
        if "mobile_number" not in user_columns:
            self._con.execute("ALTER TABLE users ADD COLUMN mobile_number TEXT")
        if "password_setup_hash" not in user_columns:
            self._con.execute("ALTER TABLE users ADD COLUMN password_setup_hash TEXT")
        if "password_setup_expires_at" not in user_columns:
            self._con.execute("ALTER TABLE users ADD COLUMN password_setup_expires_at TEXT")
        question_columns = {row[1] for row in self._con.execute("PRAGMA table_info(rep_questions)")}
        if "answered_by" not in question_columns:
            self._con.execute("ALTER TABLE rep_questions ADD COLUMN answered_by TEXT")

    def purge_older_than(self, days: int) -> int:
        """מחיקת שיחות (ושאלות הנציג שלהן) שלא עודכנו X ימים — נתוני שכר לא נשמרים לנצח."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock, self._con:
            old = [r[0] for r in self._con.execute("SELECT id FROM conversations WHERE updated_at < ?", (cutoff,))]
            self._con.executemany("DELETE FROM rep_questions WHERE conversation_id = ?", [(i,) for i in old])
            self._con.executemany("DELETE FROM conversations WHERE id = ?", [(i,) for i in old])
            self._con.execute("DELETE FROM employee_profiles WHERE updated_at < ?", (cutoff,))
        return len(old)

    # ---------- שיחות ----------

    def get(self, conversation_id: str) -> ConversationRecord | None:
        row = self._con.execute("SELECT data FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            return None
        record = ConversationRecord.model_validate_json(row[0])
        record.state.pending_questions = self.questions(conversation_id=conversation_id, status="open")
        return record

    def save(self, record: ConversationRecord) -> None:
        record.state.pending_questions = self.questions(conversation_id=record.state.conversation_id, status="open")
        updated_at = _now()
        with self._lock, self._con:
            owner = self._con.execute(
                "SELECT owner_id FROM conversations WHERE id = ?",
                (record.state.conversation_id,),
            ).fetchone()
            self._con.execute(
                """INSERT INTO conversations (id, data, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
                (record.state.conversation_id, record.model_dump_json(), updated_at),
            )
            if owner is not None and owner[0] is not None:
                self._con.execute(
                    """INSERT INTO employee_profiles (owner_id, data, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(owner_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
                    (owner[0], record.state.profile.model_dump_json(), updated_at),
                )

    def save_owned(self, record: ConversationRecord, owner_id: str) -> None:
        record.state.pending_questions = self.questions(conversation_id=record.state.conversation_id, status="open")
        updated_at = _now()
        with self._lock, self._con:
            row = self._con.execute(
                "SELECT owner_id FROM conversations WHERE id = ?",
                (record.state.conversation_id,),
            ).fetchone()
            if row is not None and row[0] != owner_id:
                raise ValueError("Conversation belongs to another user")
            self._con.execute(
                """INSERT INTO conversations (id, data, updated_at, owner_id) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data = excluded.data,
                    updated_at = excluded.updated_at, owner_id = excluded.owner_id""",
                (record.state.conversation_id, record.model_dump_json(), updated_at, owner_id),
            )
            self._con.execute(
                """INSERT INTO employee_profiles (owner_id, data, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
                (owner_id, record.state.profile.model_dump_json(), updated_at),
            )

    def get_profile(self, owner_id: str) -> EmployeeProfile | None:
        row = self._con.execute(
            "SELECT data FROM employee_profiles WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        if row is None:
            # Adopt profiles from conversations saved before cross-conversation persistence was added.
            row = self._con.execute(
                "SELECT data FROM conversations WHERE owner_id = ? ORDER BY updated_at DESC LIMIT 1",
                (owner_id,),
            ).fetchone()
            if row is None:
                return None
            return ConversationRecord.model_validate_json(row[0]).state.profile
        return EmployeeProfile.model_validate_json(row[0])

    def get_owned(self, conversation_id: str, owner_id: str) -> ConversationRecord | None:
        row = self._con.execute(
            "SELECT data FROM conversations WHERE id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        ).fetchone()
        if row is None:
            return None
        record = ConversationRecord.model_validate_json(row[0])
        record.state.pending_questions = self.questions(conversation_id=conversation_id, status="open")
        return record

    def create_account(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        role: str,
        full_name: str | None = None,
        mobile_number: str | None = None,
    ) -> bool:
        if role not in {"employee", "rep"}:
            raise ValueError("Public account creation is limited to employee and representative roles.")
        try:
            with self._lock, self._con:
                self._con.execute(
                    """INSERT INTO users
                    (id, email, password_hash, created_at, role, full_name, mobile_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, email, password_hash, _now(), role, full_name, mobile_number),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    # ---------- הזמנות: המנהל/ת מנפיק/ה קוד, העובד/ת או הנציג/ה נרשמים איתו ----------

    def create_invitation(
        self,
        user_id: str,
        email: str,
        role: str,
        code_hash: str,
        expires_at: str,
        full_name: str | None = None,
        mobile_number: str | None = None,
    ) -> str:
        """חשבון ממתין עם קוד. הזמנה חוזרת לאותו דוא״ל מחליפה את הקוד הקודם.

        מחזיר "created", או "exists" אם כבר יש חשבון פעיל או הזמנה לתפקיד אחר בדוא״ל הזה.
        """
        if role not in {"employee", "rep"}:
            raise ValueError("Invitations are limited to employee and representative roles.")
        with self._lock, self._con:
            row = self._con.execute(
                "SELECT role, password_hash FROM users WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()
            if row is not None:
                if row[0] != role or row[1] != "pending":
                    return "exists"
                self._con.execute(
                    """UPDATE users SET password_setup_hash = ?, password_setup_expires_at = ?,
                        full_name = ?, mobile_number = ?
                    WHERE lower(email) = lower(?)""",
                    (code_hash, expires_at, full_name, mobile_number, email),
                )
                self._con.execute(
                    "DELETE FROM employee_setup_attempts WHERE lower(email) = lower(?)",
                    (email,),
                )
                return "created"
            try:
                self._con.execute(
                    """INSERT INTO users
                    (id, email, password_hash, created_at, role, full_name, mobile_number,
                     password_setup_hash, password_setup_expires_at)
                    VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
                    (user_id, email, _now(), role, full_name, mobile_number, code_hash, expires_at),
                )
            except sqlite3.IntegrityError:
                return "exists"
            return "created"

    def invitation_role(self, email: str, code_hash: str) -> str | None:
        """התפקיד של הזמנה תקפה, או None אם הקוד שגוי, פג תוקפו או נוצל."""
        row = self._con.execute(
            """SELECT role FROM users
            WHERE lower(email) = lower(?) AND password_hash = 'pending'
                AND password_setup_hash = ? AND password_setup_expires_at > ?""",
            (email, code_hash, _now()),
        ).fetchone()
        return row[0] if row else None

    def accept_invitation(self, email: str, code_hash: str, password_hash: str) -> str | None:
        """מפעיל את החשבון הממתין עם הסיסמה שנבחרה. הקוד חד-פעמי. מחזיר את מזהה המשתמש."""
        with self._lock, self._con:
            row = self._con.execute(
                """SELECT id FROM users
                WHERE lower(email) = lower(?) AND password_hash = 'pending'
                    AND password_setup_hash = ? AND password_setup_expires_at > ?""",
                (email, code_hash, _now()),
            ).fetchone()
            if row is None:
                return None
            self._con.execute(
                """UPDATE users SET password_hash = ?, password_setup_hash = NULL,
                    password_setup_expires_at = NULL
                WHERE id = ?""",
                (password_hash, row[0]),
            )
            return row[0]

    def registration_attempt_allowed(
        self,
        email: str,
        client_ip: str,
        max_attempts: int = 10,
        window_seconds: int = 900,
    ) -> bool:
        now = time.time()
        with self._lock, self._con:
            self._con.execute(
                "DELETE FROM employee_setup_attempts WHERE window_started_at <= ?",
                (now - window_seconds,),
            )
            row = self._con.execute(
                """SELECT attempts FROM employee_setup_attempts
                WHERE lower(email) = lower(?) AND client_ip = ?""",
                (email, client_ip),
            ).fetchone()
            return row is None or row[0] < max_attempts

    def record_registration_failure(
        self,
        email: str,
        client_ip: str,
        window_seconds: int = 900,
    ) -> None:
        now = time.time()
        with self._lock, self._con:
            self._con.execute(
                "DELETE FROM employee_setup_attempts WHERE window_started_at <= ?",
                (now - window_seconds,),
            )
            pending = self._con.execute(
                "SELECT 1 FROM users WHERE lower(email) = lower(?) AND password_hash = 'pending'",
                (email,),
            ).fetchone()
            if pending is None:
                return
            row = self._con.execute(
                """SELECT attempts, window_started_at FROM employee_setup_attempts
                WHERE lower(email) = lower(?) AND client_ip = ?""",
                (email, client_ip),
            ).fetchone()
            if row is None or now - row[1] >= window_seconds:
                self._con.execute(
                    """INSERT INTO employee_setup_attempts
                    (email, client_ip, attempts, window_started_at) VALUES (?, ?, 1, ?)
                    ON CONFLICT(email, client_ip) DO UPDATE SET
                    attempts = 1, window_started_at = excluded.window_started_at""",
                    (email, client_ip, now),
                )
            else:
                self._con.execute(
                    """UPDATE employee_setup_attempts SET attempts = attempts + 1
                    WHERE lower(email) = lower(?) AND client_ip = ?""",
                    (email, client_ip),
                )

    def clear_registration_attempts(self, email: str, client_ip: str) -> None:
        with self._lock, self._con:
            self._con.execute(
                """DELETE FROM employee_setup_attempts
                WHERE lower(email) = lower(?) AND client_ip = ?""",
                (email, client_ip),
            )

    def upsert_demo_rep(self, email: str, password_hash: str) -> bool:
        """חשבון נציג/ה קבוע לדמו (מ-.env), כדי שאפשר יהיה להריץ את כל התסריט גם אחרי פריסה."""
        try:
            with self._lock, self._con:
                self._con.execute(
                    """INSERT INTO users
                    (id, email, password_hash, created_at, role, full_name)
                    VALUES ('demo-rep', ?, ?, ?, 'rep', 'נציג/ת הדגמה')
                    ON CONFLICT(id) DO UPDATE SET
                        email = excluded.email, password_hash = excluded.password_hash, role = 'rep'""",
                    (email, password_hash, _now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def upsert_admin_account(self, email: str, password_hash: str) -> bool:
        try:
            with self._lock, self._con:
                self._con.execute(
                    """INSERT INTO users
                    (id, email, password_hash, created_at, role)
                    VALUES ('system-admin', ?, ?, ?, 'admin')
                    ON CONFLICT(id) DO UPDATE SET
                        email = excluded.email, password_hash = excluded.password_hash, role = 'admin'""",
                    (email, password_hash, _now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def user_by_email(self, email: str) -> dict | None:
        row = self._con.execute(
            """SELECT id, email, password_hash, role, full_name, mobile_number,
                password_setup_hash, password_setup_expires_at
            FROM users WHERE email = ? COLLATE NOCASE""",
            (email,),
        ).fetchone()
        return {
            "id": row[0], "email": row[1], "password_hash": row[2], "role": row[3],
            "full_name": row[4], "mobile_number": row[5], "password_setup_hash": row[6],
            "password_setup_expires_at": row[7],
        } if row else None

    def create_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        with self._lock, self._con:
            self._con.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (_now(),))
            self._con.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )

    def session_user(self, token_hash: str) -> dict | None:
        row = self._con.execute(
            """SELECT users.id, users.email, users.role, users.full_name, users.mobile_number
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?""",
            (token_hash, _now()),
        ).fetchone()
        return {
            "id": row[0], "email": row[1], "role": row[2],
            "full_name": row[3], "mobile_number": row[4],
        } if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._lock, self._con:
            self._con.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))

    # ---------- דוח נקודות הבלבול ----------

    def add_topic_events(self, month: str, conversation_hash: str, events: list[tuple[str, str]]) -> None:
        now = _now()
        with self._lock, self._con:
            self._con.executemany(
                "INSERT INTO topic_events (month, kind, topic, conversation_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                [(month, kind, topic, conversation_hash, now) for kind, topic in events],
            )

    def topic_counts(self, month: str) -> list[tuple]:
        """(kind, topic, events, distinct conversations) לחודש. rep_any = כמה שיחות שונות הגיעו לנציג."""
        rows = [tuple(r) for r in self._con.execute(
            """SELECT kind, topic, COUNT(*), COUNT(DISTINCT conversation_hash) FROM topic_events
            WHERE month = ? GROUP BY kind, topic""",
            (month,),
        )]
        events, conversations = self._con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT conversation_hash) FROM topic_events WHERE month = ? AND kind = 'rep'",
            (month,),
        ).fetchone()
        if events:
            rows.append(("rep_any", "", events, conversations))
        return rows

    def save_monthly_report(self, month: str, counts_json: str) -> None:
        with self._lock, self._con:
            self._con.execute(
                "INSERT OR IGNORE INTO monthly_reports (month, counts, created_at) VALUES (?, ?, ?)",
                (month, counts_json, _now()),
            )

    def monthly_report(self, month: str) -> list[tuple] | None:
        row = self._con.execute("SELECT counts FROM monthly_reports WHERE month = ?", (month,)).fetchone()
        return [tuple(r) for r in json.loads(row[0])] if row else None

    def monthly_reports(self) -> list[dict]:
        rows = self._con.execute("SELECT month, created_at FROM monthly_reports ORDER BY month DESC").fetchall()
        return [{"month": m, "created_at": c} for m, c in rows]

    # ---------- שאלות לנציג ----------

    def questions(self, conversation_id: str | None = None, status: str | None = None) -> list[RepQuestion]:
        sql, params = "SELECT data FROM rep_questions WHERE 1=1", []
        if conversation_id:
            sql += " AND conversation_id = ?"
            params.append(conversation_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        return [RepQuestion.model_validate_json(r[0]) for r in self._con.execute(sql, params)]

    def question(self, question_id: str) -> RepQuestion | None:
        row = self._con.execute("SELECT data FROM rep_questions WHERE id = ?", (question_id,)).fetchone()
        return RepQuestion.model_validate_json(row[0]) if row else None

    def save_question(self, q: RepQuestion) -> None:
        with self._lock, self._con:
            self._con.execute(
                """INSERT INTO rep_questions (id, conversation_id, status, created_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data""",
                (q.id, q.conversation_id, q.status, q.created_at.isoformat(), q.model_dump_json()),
            )

    def answer_question(self, q: RepQuestion, representative_id: str) -> bool:
        with self._lock, self._con:
            cursor = self._con.execute(
                """UPDATE rep_questions SET status = ?, data = ?, answered_by = ?
                WHERE id = ? AND status = 'open' AND answered_by IS NULL""",
                (q.status, q.model_dump_json(), representative_id, q.id),
            )
            return cursor.rowcount == 1

    def questions_for_representative(self, representative_id: str, status: str | None = None) -> list[RepQuestion]:
        query = """SELECT data FROM rep_questions
            WHERE (status = 'open' OR answered_by = ?)"""
        params: list[str] = [representative_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = self._con.execute(
            query, params,
        )
        return [RepQuestion.model_validate_json(row[0]) for row in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def use_store(store: Store) -> None:
    """לבדיקות: מחליף ל-DB זמני."""
    global _store
    _store = store
