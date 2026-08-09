import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, date, timedelta
from functools import wraps
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from flask import (
    Flask, Response, jsonify, redirect, render_template, request,
    session, url_for, send_from_directory
)

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

APP_TITLE = os.getenv("APP_TITLE", "Betreuungsplan")
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "betreuung.sqlite"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_KEEP = max(5, int(os.getenv("BACKUP_KEEP", "50")))
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
APP_USER = os.getenv("APP_USER", "familie")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
ICAL_TOKEN = os.getenv("ICAL_TOKEN", "")

if AUTH_ENABLED and not APP_PASSWORD:
    raise RuntimeError("APP_PASSWORD muss gesetzt sein, wenn AUTH_ENABLED=true ist.")

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

backup_lock = threading.Lock()

DEFAULT_PEOPLE = [
    ("Vreni", "#e9def7", 10),
    ("Kita", "#e2ecef", 20),
    ("Silvia", "#dff0df", 30),
    ("Annie/Sepp", "#f6e0db", 40),
    ("Janin/Hörbi", "#f4e7c9", 50),
    ("Silvia/Selli", "#d7efdf", 60),
    ("Andere", "#ececec", 999),
]


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS people (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          color TEXT NOT NULL DEFAULT '#ececec',
          sort_order INTEGER NOT NULL DEFAULT 100
        );

        CREATE TABLE IF NOT EXISTS entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          day TEXT NOT NULL UNIQUE,
          person_id INTEGER NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_entries_day ON entries(day);

        CREATE TABLE IF NOT EXISTS periods (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          start_day TEXT NOT NULL,
          end_day TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'vacation',
          label TEXT NOT NULL DEFAULT '',
          color TEXT NOT NULL DEFAULT '#f2a65a',
          source TEXT NOT NULL DEFAULT 'manual',
          external_uid TEXT UNIQUE,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_periods_range ON periods(start_day, end_day);
        """)
        existing = conn.execute("SELECT COUNT(*) AS c FROM people").fetchone()["c"]
        if existing == 0:
            conn.executemany(
                "INSERT INTO people(name,color,sort_order) VALUES(?,?,?)",
                DEFAULT_PEOPLE,
            )


def backup_db(reason="change"):
    """Create a consistent standalone SQLite backup using SQLite's backup API."""
    with backup_lock:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dst = BACKUP_DIR / f"betreuung-{stamp}-{reason}.sqlite"
        src = sqlite3.connect(DB_PATH, timeout=10)
        target = sqlite3.connect(dst)
        try:
            src.backup(target)
        finally:
            target.close()
            src.close()

        backups = sorted(BACKUP_DIR.glob("betreuung-*.sqlite"), reverse=True)
        for old in backups[BACKUP_KEEP:]:
            try:
                old.unlink()
            except OSError:
                pass


init_db()


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not AUTH_ENABLED:
            return fn(*args, **kwargs)
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "not_authenticated"}), 401
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


@app.get("/healthz")
def healthz():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}, 200
    except Exception:
        return {"status": "error"}, 500


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        user = request.form.get("user", "")
        password = request.form.get("password", "")
        if hmac.compare_digest(user, APP_USER) and hmac.compare_digest(password, APP_PASSWORD):
            session.clear()
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Benutzername oder Passwort ist falsch."
    return render_template("login.html", title=APP_TITLE, error=error)


@app.post("/logout")
def logout():
    session.clear()
    response = redirect(url_for("login") if AUTH_ENABLED else url_for("index"))
    response.headers["Clear-Site-Data"] = '"cache"'
    return response


@app.get("/")
@login_required
def index():
    return render_template("index.html", title=APP_TITLE, auth_enabled=AUTH_ENABLED)


@app.get("/manifest.webmanifest")
def manifest():
    response = jsonify({
        "id": "/",
        "name": APP_TITLE,
        "short_name": "Betreuung",
        "description": "Familienplan für Betreuungstage",
        "lang": "de-CH",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "orientation": "any",
        "background_color": "#f7f7f2",
        "theme_color": "#305f57",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
        ],
    })
    response.mimetype = "application/manifest+json"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/service-worker.js")
def service_worker():
    response = send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def valid_iso_day(value):
    try:
        return date.fromisoformat(value).isoformat()
    except Exception:
        return None


def entry_rows(where="", params=()):
    query = """
      SELECT e.id, e.day, e.note, e.created_at, e.updated_at,
             p.id AS person_id, p.name AS person, p.color AS color
      FROM entries e
      JOIN people p ON p.id = e.person_id
    """
    if where:
        query += " WHERE " + where
    query += " ORDER BY e.day ASC"
    with db() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


@app.get("/api/config")
@login_required
def api_config():
    return jsonify({
        "title": APP_TITLE,
        "ical_enabled": bool(ICAL_TOKEN),
        "ical_url": (request.url_root.rstrip("/") + "/calendar.ics?token=" + ICAL_TOKEN) if ICAL_TOKEN else None,
        "data_file": str(DB_PATH),
        "backup_dir": str(BACKUP_DIR),
        "auth_enabled": AUTH_ENABLED,
    })


@app.get("/api/people")
@login_required
def api_people():
    with db() as conn:
        rows = conn.execute(
            "SELECT id,name,color,sort_order FROM people ORDER BY sort_order,name"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/people")
@login_required
def api_people_add():
    payload = request.get_json(force=True)
    name = str(payload.get("name", "")).strip()
    color = str(payload.get("color", "#ececec")).strip()
    if not name:
        return jsonify({"error": "Name fehlt"}), 400
    if not color.startswith("#") or len(color) not in (4, 7):
        color = "#ececec"
    try:
        with db() as conn:
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) AS m FROM people").fetchone()["m"]
            cur = conn.execute(
                "INSERT INTO people(name,color,sort_order) VALUES(?,?,?)",
                (name, color, max_order + 10),
            )
            new_id = cur.lastrowid
        backup_db("person-add")
        return jsonify({"id": new_id, "name": name, "color": color}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Diese Person existiert bereits"}), 409


@app.put("/api/people/<int:person_id>")
@login_required
def api_people_update(person_id):
    payload = request.get_json(force=True)
    name = str(payload.get("name", "")).strip()
    color = str(payload.get("color", "#ececec")).strip()
    if not name:
        return jsonify({"error": "Name fehlt"}), 400
    try:
        with db() as conn:
            conn.execute(
                "UPDATE people SET name=?,color=? WHERE id=?",
                (name, color, person_id),
            )
        backup_db("person-update")
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Diese Person existiert bereits"}), 409


@app.delete("/api/people/<int:person_id>")
@login_required
def api_people_delete(person_id):
    try:
        with db() as conn:
            conn.execute("DELETE FROM people WHERE id=?", (person_id,))
        backup_db("person-delete")
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Person wird noch in Betreuungseinträgen verwendet"}), 409


@app.get("/api/entries")
@login_required
def api_entries():
    year = request.args.get("year", "").strip()
    person = request.args.get("person", "").strip()

    clauses = []
    params = []
    if year:
        clauses.append("e.day LIKE ?")
        params.append(f"{year}-%")
    if person:
        clauses.append("p.name = ?")
        params.append(person)

    return jsonify(entry_rows(" AND ".join(clauses), tuple(params)))


@app.post("/api/entries")
@login_required
def api_entries_add():
    payload = request.get_json(force=True)
    day = valid_iso_day(str(payload.get("day", "")))
    person_id = payload.get("person_id")
    note = str(payload.get("note", "")).strip()
    if not day or not person_id:
        return jsonify({"error": "Datum und Betreuung sind erforderlich"}), 400

    now = datetime.now().isoformat(timespec="seconds")
    try:
        with db() as conn:
            existing = conn.execute("SELECT id FROM entries WHERE day=?", (day,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE entries SET person_id=?,note=?,updated_at=? WHERE id=?",
                    (person_id, note, now, existing["id"]),
                )
                entry_id = existing["id"]
                status = 200
            else:
                cur = conn.execute(
                    """INSERT INTO entries(day,person_id,note,created_at,updated_at)
                       VALUES(?,?,?,?,?)""",
                    (day, person_id, note, now, now),
                )
                entry_id = cur.lastrowid
                status = 201
        backup_db("entry")
        return jsonify({"id": entry_id, "day": day}), status
    except sqlite3.IntegrityError as exc:
        return jsonify({"error": str(exc)}), 409


@app.put("/api/entries/<int:entry_id>")
@login_required
def api_entries_update(entry_id):
    payload = request.get_json(force=True)
    day = valid_iso_day(str(payload.get("day", "")))
    person_id = payload.get("person_id")
    note = str(payload.get("note", "")).strip()
    if not day or not person_id:
        return jsonify({"error": "Datum und Betreuung sind erforderlich"}), 400

    try:
        with db() as conn:
            conn.execute(
                """UPDATE entries
                   SET day=?,person_id=?,note=?,updated_at=?
                   WHERE id=?""",
                (day, person_id, note, datetime.now().isoformat(timespec="seconds"), entry_id),
            )
        backup_db("entry-update")
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Für dieses Datum gibt es bereits einen Eintrag"}), 409


@app.delete("/api/entries/<int:entry_id>")
@login_required
def api_entries_delete(entry_id):
    with db() as conn:
        conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
    backup_db("entry-delete")
    return jsonify({"ok": True})


def parse_batch_payload():
    payload = request.get_json(force=True)
    start_day = valid_iso_day(str(payload.get("start_day", "")))
    end_day = valid_iso_day(str(payload.get("end_day", "")))
    person_id = payload.get("person_id")
    note = str(payload.get("note", "")).strip()

    try:
        person_id = int(person_id)
        weekday = int(payload.get("weekday"))
    except (TypeError, ValueError):
        return None, "Betreuung und Wochentag sind erforderlich"

    if not start_day or not end_day:
        return None, "Von und Bis sind erforderlich"
    if not 0 <= weekday <= 6:
        return None, "Ungültiger Wochentag"

    start_obj = date.fromisoformat(start_day)
    end_obj = date.fromisoformat(end_day)
    if end_obj < start_obj:
        return None, "Bis muss am oder nach Von liegen"
    if (end_obj - start_obj).days > 366 * 5:
        return None, "Der Zeitraum darf maximal fünf Jahre umfassen"

    with db() as conn:
        person = conn.execute(
            "SELECT id,name,color FROM people WHERE id=?", (person_id,)
        ).fetchone()
    if not person:
        return None, "Betreuungsperson wurde nicht gefunden"

    first_offset = (weekday - start_obj.weekday()) % 7
    current = start_obj + timedelta(days=first_offset)
    days = []
    while current <= end_obj:
        days.append(current.isoformat())
        current += timedelta(days=7)

    return {
        "start_day": start_day,
        "end_day": end_day,
        "weekday": weekday,
        "person_id": person_id,
        "person": dict(person),
        "note": note,
        "days": days,
    }, None


def batch_preview_data(batch):
    days = batch["days"]
    occupied = []
    if days:
        placeholders = ",".join("?" for _ in days)
        with db() as conn:
            rows = conn.execute(
                f"""SELECT e.day,p.name AS person
                    FROM entries e
                    JOIN people p ON p.id=e.person_id
                    WHERE e.day IN ({placeholders})
                    ORDER BY e.day""",
                tuple(days),
            ).fetchall()
        occupied = [dict(r) for r in rows]

    occupied_days = {r["day"] for r in occupied}
    create_days = [d for d in days if d not in occupied_days]
    return {
        "matched_count": len(days),
        "create_count": len(create_days),
        "skipped_count": len(occupied),
        "occupied": occupied,
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
    }


@app.post("/api/entries/batch/preview")
@login_required
def api_entries_batch_preview():
    batch, error = parse_batch_payload()
    if error:
        return jsonify({"error": error}), 400
    preview = batch_preview_data(batch)
    preview.update({
        "person": batch["person"]["name"],
        "weekday": batch["weekday"],
    })
    return jsonify(preview)


@app.post("/api/entries/batch")
@login_required
def api_entries_batch_create():
    batch, error = parse_batch_payload()
    if error:
        return jsonify({"error": error}), 400

    now = datetime.now().isoformat(timespec="seconds")
    created = 0
    with db() as conn:
        for day in batch["days"]:
            cur = conn.execute(
                """INSERT OR IGNORE INTO entries(day,person_id,note,created_at,updated_at)
                   VALUES(?,?,?,?,?)""",
                (day, batch["person_id"], batch["note"], now, now),
            )
            if cur.rowcount == 1:
                created += 1

    if created:
        backup_db("entry-batch")

    return jsonify({
        "ok": True,
        "matched_count": len(batch["days"]),
        "created_count": created,
        "skipped_count": len(batch["days"]) - created,
    }), 201 if created else 200



def valid_color(value, fallback="#ececec"):
    value = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    return fallback


def period_rows(year=""):
    query = """
      SELECT id,start_day,end_day,kind,label,color,source,external_uid,created_at,updated_at
      FROM periods
    """
    params = []
    if year:
        query += " WHERE start_day <= ? AND end_day >= ?"
        params = [f"{year}-12-31", f"{year}-01-01"]
    query += " ORDER BY start_day ASC, end_day ASC, label ASC"
    with db() as conn:
        return [dict(r) for r in conn.execute(query, tuple(params)).fetchall()]


@app.get("/api/periods")
@login_required
def api_periods():
    year = request.args.get("year", "").strip()
    return jsonify(period_rows(year))


@app.post("/api/periods")
@login_required
def api_periods_add():
    payload = request.get_json(force=True)
    start_day = valid_iso_day(str(payload.get("start_day", "")))
    end_day = valid_iso_day(str(payload.get("end_day", "")))
    kind = str(payload.get("kind", "vacation")).strip() or "vacation"
    label = str(payload.get("label", "")).strip()
    default_color = "#f2a65a" if kind == "vacation" else "#d65a6f" if kind == "holiday" else "#80a4c2"
    color = valid_color(payload.get("color"), default_color)
    if not start_day or not end_day:
        return jsonify({"error": "Start- und Enddatum sind erforderlich"}), 400
    if end_day < start_day:
        return jsonify({"error": "Enddatum muss nach dem Startdatum liegen"}), 400
    if not label:
        label = "Ferien" if kind == "vacation" else "Feiertag" if kind == "holiday" else "Zeitraum"
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO periods(start_day,end_day,kind,label,color,source,created_at,updated_at)
               VALUES(?,?,?,?,?,'manual',?,?)""",
            (start_day, end_day, kind, label, color, now, now),
        )
        period_id = cur.lastrowid
    backup_db("period-add")
    return jsonify({"id": period_id}), 201


@app.put("/api/periods/<int:period_id>")
@login_required
def api_periods_update(period_id):
    payload = request.get_json(force=True)
    start_day = valid_iso_day(str(payload.get("start_day", "")))
    end_day = valid_iso_day(str(payload.get("end_day", "")))
    kind = str(payload.get("kind", "vacation")).strip() or "vacation"
    label = str(payload.get("label", "")).strip()
    default_color = "#f2a65a" if kind == "vacation" else "#d65a6f" if kind == "holiday" else "#80a4c2"
    color = valid_color(payload.get("color"), default_color)
    if not start_day or not end_day or end_day < start_day:
        return jsonify({"error": "Ungültiger Zeitraum"}), 400
    if not label:
        label = "Ferien" if kind == "vacation" else "Feiertag" if kind == "holiday" else "Zeitraum"
    with db() as conn:
        conn.execute(
            """UPDATE periods SET start_day=?,end_day=?,kind=?,label=?,color=?,updated_at=?
               WHERE id=?""",
            (start_day, end_day, kind, label, color, datetime.now().isoformat(timespec="seconds"), period_id),
        )
    backup_db("period-update")
    return jsonify({"ok": True})


@app.delete("/api/periods/<int:period_id>")
@login_required
def api_periods_delete(period_id):
    with db() as conn:
        conn.execute("DELETE FROM periods WHERE id=?", (period_id,))
    backup_db("period-delete")
    return jsonify({"ok": True})


def unfold_ics_lines(raw):
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def ics_unescape(value):
    return (str(value or "")
            .replace("\\n", "\n")
            .replace("\\N", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\"))


def parse_ics_date(value):
    value = str(value or "").strip()
    match = re.search(r"(\d{8})", value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def parse_ics_events(raw):
    events = []
    current = None
    for line in unfold_ics_lines(raw):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            continue
        if upper == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        left, value = line.split(":", 1)
        name = left.split(";", 1)[0].upper()
        current[name] = (left, value)
    return events


def expand_ics_dates(event):
    dtstart_prop = event.get("DTSTART")
    if not dtstart_prop:
        return []
    start_obj = parse_ics_date(dtstart_prop[1])
    if not start_obj:
        return []

    dtend_prop = event.get("DTEND")
    end_obj = parse_ics_date(dtend_prop[1]) if dtend_prop else start_obj
    if not end_obj:
        end_obj = start_obj
    if dtend_prop and "VALUE=DATE" in dtend_prop[0].upper() and end_obj > start_obj:
        end_obj -= timedelta(days=1)
    if end_obj < start_obj:
        end_obj = start_obj
    duration = end_obj - start_obj

    rrule_prop = event.get("RRULE")
    if not rrule_prop or "FREQ=YEARLY" not in rrule_prop[1].upper():
        return [(start_obj, end_obj, None)]

    rule = {}
    for part in rrule_prop[1].split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            rule[key.upper()] = value

    month = int(rule.get("BYMONTH", start_obj.month))
    monthday = int(rule.get("BYMONTHDAY", start_obj.day))
    until_obj = parse_ics_date(rule.get("UNTIL", "")) if rule.get("UNTIL") else None
    current_year = date.today().year
    first_year = max(start_obj.year, current_year - 1)
    last_year = current_year + 5
    if until_obj:
        last_year = min(last_year, until_obj.year)
    dates = []
    for year in range(first_year, last_year + 1):
        try:
            occurrence_start = date(year, month, monthday)
        except ValueError:
            continue
        if occurrence_start < start_obj:
            continue
        if until_obj and occurrence_start > until_obj:
            continue
        dates.append((occurrence_start, occurrence_start + duration, year))
    return dates


@app.post("/import.ics")
@login_required
def import_ics():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Keine ICS-Datei ausgewählt"}), 400
    raw_bytes = file.read()
    try:
        raw = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = raw_bytes.decode("latin-1")

    kind = request.form.get("kind", "holiday").strip() or "holiday"
    default_color = "#d65a6f" if kind == "holiday" else "#f2a65a"
    color = valid_color(request.form.get("color"), default_color)
    imported = 0
    skipped = 0
    now = datetime.now().isoformat(timespec="seconds")

    with db() as conn:
        for event in parse_ics_events(raw):
            occurrences = expand_ics_dates(event)
            if not occurrences:
                skipped += 1
                continue

            summary = ics_unescape(event.get("SUMMARY", ("SUMMARY", "Feiertag"))[1]).strip() or "Feiertag"
            uid = ics_unescape(event.get("UID", ("UID", ""))[1]).strip()

            for start_obj, end_obj, recurrence_year in occurrences:
                base_uid = uid if uid else f"{start_obj.isoformat()}:{end_obj.isoformat()}:{summary}"
                external_uid = "ics:" + base_uid + (f":{recurrence_year}" if recurrence_year else "")

                conn.execute(
                    """INSERT INTO periods(start_day,end_day,kind,label,color,source,external_uid,created_at,updated_at)
                       VALUES(?,?,?,?,?,'ics',?,?,?)
                       ON CONFLICT(external_uid) DO UPDATE SET
                         start_day=excluded.start_day,
                         end_day=excluded.end_day,
                         kind=excluded.kind,
                         label=excluded.label,
                         color=excluded.color,
                         source='ics',
                         updated_at=excluded.updated_at""",
                    (start_obj.isoformat(), end_obj.isoformat(), kind, summary, color, external_uid, now, now),
                )
                imported += 1

    if imported:
        backup_db("ics-import")
    return jsonify({"ok": True, "imported": imported, "skipped": skipped})

def filtered_entry_rows(year="", people=None, search=""):
    people = [p for p in (people or []) if p]
    clauses = []
    params = []
    if year:
        clauses.append("e.day LIKE ?")
        params.append(f"{year}-%")
    if people:
        placeholders = ",".join("?" for _ in people)
        clauses.append(f"p.name IN ({placeholders})")
        params.extend(people)
    if search:
        clauses.append("(LOWER(e.note) LIKE ? OR LOWER(p.name) LIKE ?)")
        needle = f"%{search.lower()}%"
        params.extend([needle, needle])
    return entry_rows(" AND ".join(clauses), tuple(params)) if clauses else entry_rows()


def export_filename(extension, year="", people=None):
    people = [p for p in (people or []) if p]
    parts = ["betreuung"]
    if len(people) == 1:
        safe_person = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "-" for c in people[0]).strip("-")
        if safe_person:
            parts.append(safe_person)
    elif len(people) > 1:
        parts.append(f"auswahl-{len(people)}")
    if year:
        parts.append(year)
    if len(parts) == 1:
        parts.append("alle")
    return "-".join(parts) + extension


@app.get("/export.csv")
@login_required
def export_csv():
    year = request.args.get("year", "").strip()
    people = [p.strip() for p in request.args.getlist("person") if p.strip()]
    search = request.args.get("q", "").strip()
    rows = filtered_entry_rows(year, people, search)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Datum", "Betreuung", "Bemerkung"])
    for r in rows:
        w.writerow([r["day"], r["person"], r["note"]])

    body = "\ufeff" + buf.getvalue()
    return Response(
        body,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{export_filename(".csv", year, people)}"'},
    )


@app.get("/export.pdf")
@login_required
def export_pdf():
    year = request.args.get("year", "").strip()
    people = [p.strip() for p in request.args.getlist("person") if p.strip()]
    search = request.args.get("q", "").strip()
    rows = filtered_entry_rows(year, people, search)

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Betreuungsliste {', '.join(people) if people else 'Alle'} {year}".strip(),
        author=APP_TITLE,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ListTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=19,
        textColor=colors.HexColor("#1e2524"),
        spaceAfter=4 * mm,
    )
    meta_style = ParagraphStyle(
        "ListMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#68716f"),
        spaceAfter=5 * mm,
    )
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=10.5,
        textColor=colors.HexColor("#1e2524"),
    )
    header_style = ParagraphStyle(
        "Header",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )

    if not people:
        label = "Alle Betreuungspersonen"
    elif len(people) <= 3:
        label = ", ".join(people)
    else:
        label = f"{len(people)} ausgewählte Betreuungspersonen"
    if year:
        label += f" - {year}"
    if search:
        label += f" - Filter: {search}"

    story = [
        Paragraph("Betreuungsliste", title_style),
        Paragraph(f"{xml_escape(label)}<br/>{len(rows)} Betreuungstage", meta_style),
    ]

    data = [[
        Paragraph("Datum", header_style),
        Paragraph("Tag", header_style),
        Paragraph("Betreuung", header_style),
        Paragraph("Bemerkung", header_style),
    ]]
    weekdays = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    row_colors = []
    for idx, r in enumerate(rows, start=1):
        day_obj = date.fromisoformat(r["day"])
        data.append([
            Paragraph(day_obj.strftime("%d.%m.%Y"), cell_style),
            Paragraph(weekdays[day_obj.weekday()], cell_style),
            Paragraph(xml_escape(r["person"]), cell_style),
            Paragraph(xml_escape(r["note"] or ""), cell_style),
        ])
        try:
            row_colors.append((idx, colors.HexColor(r["color"])))
        except Exception:
            row_colors.append((idx, colors.HexColor("#ececec")))

    table = Table(
        data,
        colWidths=[29 * mm, 18 * mm, 43 * mm, 96 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#305f57")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dfe4e1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for row_index, bg in row_colors:
        table_style.append(("BACKGROUND", (2, row_index), (2, row_index), bg))
    table.setStyle(TableStyle(table_style))
    story.append(table)

    def footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#68716f"))
        canvas.drawString(12 * mm, 7 * mm, APP_TITLE)
        canvas.drawRightString(A4[0] - 12 * mm, 7 * mm, f"Seite {doc_obj.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pdf_bytes = output.getvalue()
    output.close()
    filename = export_filename(".pdf", year, people)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@app.post("/import.csv")
@login_required
def import_csv():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Keine Datei"}), 400
    raw = file.read().decode("utf-8-sig")
    sample = raw[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
    except Exception:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(raw), dialect=dialect)

    imported = 0
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        people = {
            r["name"]: r["id"]
            for r in conn.execute("SELECT id,name FROM people").fetchall()
        }
        for row in reader:
            day = valid_iso_day((row.get("Datum") or row.get("date") or row.get("day") or "").strip())
            person_name = (row.get("Betreuung") or row.get("person") or "").strip()
            note = (row.get("Bemerkung") or row.get("note") or "").strip()
            if not day or not person_name:
                continue
            person_id = people.get(person_name)
            if not person_id:
                cur = conn.execute(
                    "INSERT INTO people(name,color,sort_order) VALUES(?,?,?)",
                    (person_name, "#ececec", 500),
                )
                person_id = cur.lastrowid
                people[person_name] = person_id
            conn.execute(
                """INSERT INTO entries(day,person_id,note,created_at,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(day) DO UPDATE SET
                     person_id=excluded.person_id,
                     note=excluded.note,
                     updated_at=excluded.updated_at""",
                (day, person_id, note, now, now),
            )
            imported += 1
    backup_db("import")
    return jsonify({"ok": True, "imported": imported})


def ics_escape(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


@app.get("/calendar.ics")
def calendar_ics():
    if not ICAL_TOKEN or not hmac.compare_digest(request.args.get("token", ""), ICAL_TOKEN):
        return Response("Not found", status=404)

    person = request.args.get("person", "").strip()
    rows = entry_rows("p.name = ?", (person,)) if person else entry_rows()
    host = request.host.split(":")[0]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Betreuungsplan//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(APP_TITLE)}",
    ]
    for r in rows:
        d = r["day"].replace("-", "")
        dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:betreuung-{r['id']}@{host}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{d}",
            f"SUMMARY:{ics_escape(r['person'])}",
            f"DESCRIPTION:{ics_escape(r['note'])}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines) + "\r\n"
    return Response(body, mimetype="text/calendar; charset=utf-8")


@app.get("/api/stats")
@login_required
def api_stats():
    year = request.args.get("year", str(date.today().year))
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM entries WHERE day LIKE ?", (f"{year}-%",)
        ).fetchone()["c"]
        per_person = [
            dict(r) for r in conn.execute(
                """SELECT p.name, p.color, COUNT(e.id) AS count
                   FROM people p
                   LEFT JOIN entries e ON e.person_id=p.id AND e.day LIKE ?
                   GROUP BY p.id
                   HAVING count > 0
                   ORDER BY count DESC, p.name""",
                (f"{year}-%",),
            ).fetchall()
        ]
    return jsonify({"year": year, "total": total, "per_person": per_person})
