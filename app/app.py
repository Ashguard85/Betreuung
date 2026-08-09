import csv
import hmac
import io
import json
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
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Flowable

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


class YearOverviewCell(Flowable):
    """Compact annual-plan cell with optional caregiver fill and period rail."""

    def __init__(self, width, height, entry=None, periods=None):
        super().__init__()
        self.width = width
        self.height = height
        self.entry = entry
        self.periods = periods or []

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        inset_x = 0.7
        inset_y = 0.7
        rail_w = self.width * 0.18 if self.periods else 0
        gap = 0.8 if self.periods else 0
        fill_w = self.width - (2 * inset_x) - rail_w - gap
        fill_h = self.height - (2 * inset_y)

        if self.entry:
            try:
                bg = colors.HexColor(self.entry.get("color") or "#ececec")
            except Exception:
                bg = colors.HexColor("#ececec")
            c.setFillColor(bg)
            c.roundRect(inset_x, inset_y, max(1, fill_w), max(1, fill_h), 2.2, stroke=0, fill=1)

            label = str(self.entry.get("person") or "")
            max_text_w = max(5, fill_w - 3)
            font_size = 4.8
            while font_size > 3.4 and c.stringWidth(label, "Helvetica-Bold", font_size) > max_text_w:
                font_size -= 0.25
            if c.stringWidth(label, "Helvetica-Bold", font_size) > max_text_w:
                while len(label) > 2 and c.stringWidth(label + "…", "Helvetica-Bold", font_size) > max_text_w:
                    label = label[:-1]
                label += "…"
            c.setFont("Helvetica-Bold", font_size)
            c.setFillColor(colors.HexColor("#1e2524"))
            c.drawCentredString(inset_x + fill_w / 2, (self.height - font_size) / 2 + 0.7, label)

        if self.periods:
            x = self.width - inset_x - rail_w
            rail_h = fill_h / len(self.periods)
            for idx, period in enumerate(self.periods):
                try:
                    color = colors.HexColor(period.get("color") or "#f2a65a")
                except Exception:
                    color = colors.HexColor("#f2a65a")
                c.setFillColor(color)
                y = inset_y + idx * rail_h
                c.rect(x, y, rail_w, rail_h + 0.15, stroke=0, fill=1)


def year_export_filename(year):
    return f"jahresplan-{year}.pdf"


def _legend_item(label, color_value, width=42 * mm):
    try:
        swatch = colors.HexColor(color_value)
    except Exception:
        swatch = colors.HexColor("#ececec")
    label_style = ParagraphStyle(
        "LegendLabel",
        fontName="Helvetica",
        fontSize=5.5,
        leading=6.0,
        textColor=colors.HexColor("#1e2524"),
    )
    item = Table(
        [["", Paragraph(xml_escape(label), label_style)]],
        colWidths=[4 * mm, width - 4 * mm],
        rowHeights=[3.7 * mm],
    )
    item.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), swatch),
        ("BOX", (0, 0), (0, 0), 0.3, colors.HexColor("#cfd5d2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return item

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
            "Cache-Control": "no-store",
        },
    )


@app.get("/export-year.pdf")
@login_required
def export_year_pdf():
    raw_year = request.args.get("year", "").strip()
    try:
        year = int(raw_year)
    except ValueError:
        year = date.today().year
    if year < 1900 or year > 2200:
        year = date.today().year

    year_entries = entry_rows("e.day LIKE ?", (f"{year}-%",))
    by_day = {row["day"]: row for row in year_entries}
    year_periods = period_rows(str(year))

    periods_by_day = {}
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    for period in year_periods:
        start_obj = max(date.fromisoformat(period["start_day"]), year_start)
        end_obj = min(date.fromisoformat(period["end_day"]), year_end)
        current = start_obj
        while current <= end_obj:
            periods_by_day.setdefault(current.isoformat(), []).append(period)
            current += timedelta(days=1)

    output = io.BytesIO()
    page_size = landscape(A4)
    doc = SimpleDocTemplate(
        output,
        pagesize=page_size,
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=7 * mm,
        bottomMargin=7 * mm,
        title=f"Jahresplan {year}",
        author=APP_TITLE,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "YearTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=9.2,
        leading=10.2,
        textColor=colors.HexColor("#1e2524"),
        spaceAfter=0.8 * mm,
    )
    head_style = ParagraphStyle(
        "YearHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=5.1,
        leading=5.5,
        alignment=1,
        textColor=colors.HexColor("#1e2524"),
    )

    months = ["Januar", "Februar", "März", "April", "Mai", "Juni",
              "Juli", "August", "September", "Oktober", "November", "Dezember"]
    usable_w = page_size[0] - doc.leftMargin - doc.rightMargin
    day_col_w = 7.2 * mm
    month_w = (usable_w - day_col_w) / 12
    header_h = 4.5 * mm
    day_h = 4.1 * mm

    data = [[Paragraph("Tag", head_style)] + [Paragraph(m, head_style) for m in months]]
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dfe4e1")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f7f6")),
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#fafbfa")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (0, -1), 5.1),
    ]

    for day_num in range(1, 32):
        row = [str(day_num)]
        pdf_row = day_num
        for month_idx in range(1, 13):
            try:
                d = date(year, month_idx, day_num)
                valid = True
            except ValueError:
                valid = False

            if not valid:
                row.append("")
                style_cmds.append(("BACKGROUND", (month_idx, pdf_row), (month_idx, pdf_row), colors.HexColor("#f1f2f1")))
                continue

            iso = d.isoformat()
            if d.weekday() >= 5:
                style_cmds.append(("BACKGROUND", (month_idx, pdf_row), (month_idx, pdf_row), colors.HexColor("#fff2b9")))
            row.append(YearOverviewCell(month_w, day_h, by_day.get(iso), periods_by_day.get(iso, [])))
        data.append(row)

    year_table = Table(
        data,
        colWidths=[day_col_w] + [month_w] * 12,
        rowHeights=[header_h] + [day_h] * 31,
        hAlign="LEFT",
    )
    year_table.setStyle(TableStyle(style_cmds))

    legend_items = []
    used_people = []
    seen_people = set()
    for entry in year_entries:
        key = (entry["person"], entry["color"])
        if key not in seen_people:
            seen_people.add(key)
            used_people.append(key)
    legend_items.extend(used_people)
    legend_items.append(("Wochenende", "#fff2b9"))

    kind_names = {"vacation": "Ferien", "holiday": "Feiertage", "other": "Markierung"}
    seen_period_legend = set()
    for period in year_periods:
        key = (kind_names.get(period.get("kind"), period.get("label") or "Markierung"), period.get("color") or "#80a4c2")
        if key not in seen_period_legend:
            seen_period_legend.add(key)
            legend_items.append(key)

    legend_rows = []
    per_row = 7
    for i in range(0, len(legend_items), per_row):
        chunk = legend_items[i:i + per_row]
        row = [_legend_item(label, color_value, width=(usable_w / per_row) - 1 * mm) for label, color_value in chunk]
        row += [""] * (per_row - len(row))
        legend_rows.append(row)
    legend = Table(legend_rows, colWidths=[usable_w / per_row] * per_row, hAlign="LEFT") if legend_rows else None
    if legend:
        legend.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 0.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
        ]))

    story = [Paragraph(f"Jahresplan {year}", title_style), year_table, Spacer(1, 0.8 * mm)]
    if legend:
        story.append(legend)

    def footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 6)
        canvas.setFillColor(colors.HexColor("#68716f"))
        canvas.drawString(8 * mm, 3.2 * mm, APP_TITLE)
        canvas.drawRightString(page_size[0] - 8 * mm, 3.2 * mm, f"Jahresplan {year}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pdf_bytes = output.getvalue()
    output.close()
    filename = year_export_filename(year)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
            "Cache-Control": "no-store",
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


@app.get("/export-data.json")
@login_required
def export_data_json():
    """Portable full backup of all user-managed application data."""
    with db() as conn:
        people = [dict(r) for r in conn.execute(
            "SELECT name,color,sort_order FROM people ORDER BY sort_order,name"
        ).fetchall()]
        entries = [dict(r) for r in conn.execute(
            """SELECT e.day,p.name AS person,e.note,e.created_at,e.updated_at
               FROM entries e JOIN people p ON p.id=e.person_id
               ORDER BY e.day"""
        ).fetchall()]
        periods = [dict(r) for r in conn.execute(
            """SELECT start_day,end_day,kind,label,color,source,external_uid,created_at,updated_at
               FROM periods ORDER BY start_day,end_day,label"""
        ).fetchall()]

    payload = {
        "format": "betreuungsplan-backup",
        "version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "app_title": APP_TITLE,
        "people": people,
        "entries": entries,
        "periods": periods,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"betreuungsplan-backup-{date.today().isoformat()}.json"
    return Response(
        raw,
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/import-data.json")
@login_required
def import_data_json():
    """Replace all user-managed data from a versioned portable JSON backup."""
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Keine Backup-Datei ausgewählt"}), 400

    raw = file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        return jsonify({"error": "Backup-Datei ist größer als 5 MB"}), 400
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify({"error": "Ungültige JSON-Backup-Datei"}), 400

    if not isinstance(payload, dict) or payload.get("format") != "betreuungsplan-backup":
        return jsonify({"error": "Diese Datei ist kein Betreuungsplan-Backup"}), 400
    if payload.get("version") != 1:
        return jsonify({"error": f"Nicht unterstützte Backup-Version: {payload.get('version')}"}), 400

    people = payload.get("people")
    entries = payload.get("entries")
    periods = payload.get("periods")
    if not isinstance(people, list) or not isinstance(entries, list) or not isinstance(periods, list):
        return jsonify({"error": "Backup ist unvollständig"}), 400
    if not people:
        return jsonify({"error": "Backup enthält keine Betreuungspersonen"}), 400

    normalized_people = []
    seen_names = set()
    for idx, item in enumerate(people):
        if not isinstance(item, dict):
            return jsonify({"error": f"Ungültige Person an Position {idx + 1}"}), 400
        name = str(item.get("name", "")).strip()
        if not name or name in seen_names:
            return jsonify({"error": f"Ungültiger oder doppelter Personenname: {name or '(leer)'}"}), 400
        seen_names.add(name)
        try:
            sort_order = int(item.get("sort_order", (idx + 1) * 10))
        except (TypeError, ValueError):
            sort_order = (idx + 1) * 10
        normalized_people.append((name, valid_color(item.get("color"), "#ececec"), sort_order))

    normalized_entries = []
    seen_days = set()
    for idx, item in enumerate(entries):
        if not isinstance(item, dict):
            return jsonify({"error": f"Ungültiger Eintrag an Position {idx + 1}"}), 400
        day = valid_iso_day(str(item.get("day", "")))
        person = str(item.get("person", "")).strip()
        if not day or not person or person not in seen_names or day in seen_days:
            return jsonify({"error": f"Ungültiger Betreuungseintrag an Position {idx + 1}"}), 400
        seen_days.add(day)
        now = datetime.now().isoformat(timespec="seconds")
        normalized_entries.append((
            day, person, str(item.get("note", "")),
            str(item.get("created_at") or now), str(item.get("updated_at") or now),
        ))

    normalized_periods = []
    seen_external_uids = set()
    for idx, item in enumerate(periods):
        if not isinstance(item, dict):
            return jsonify({"error": f"Ungültiger Zeitraum an Position {idx + 1}"}), 400
        start_day = valid_iso_day(str(item.get("start_day", "")))
        end_day = valid_iso_day(str(item.get("end_day", "")))
        if not start_day or not end_day or end_day < start_day:
            return jsonify({"error": f"Ungültiger Zeitraum an Position {idx + 1}"}), 400
        kind = str(item.get("kind", "vacation")).strip() or "vacation"
        label = str(item.get("label", "")).strip() or ("Ferien" if kind == "vacation" else "Feiertag" if kind == "holiday" else "Zeitraum")
        default_color = "#f2a65a" if kind == "vacation" else "#d65a6f" if kind == "holiday" else "#80a4c2"
        external_uid = str(item.get("external_uid")).strip() if item.get("external_uid") else None
        if external_uid and external_uid in seen_external_uids:
            external_uid = None
        if external_uid:
            seen_external_uids.add(external_uid)
        now = datetime.now().isoformat(timespec="seconds")
        normalized_periods.append((
            start_day, end_day, kind, label, valid_color(item.get("color"), default_color),
            str(item.get("source", "manual")) or "manual", external_uid,
            str(item.get("created_at") or now), str(item.get("updated_at") or now),
        ))

    # Always save the current database before destructive restore.
    backup_db("before-full-import")
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM entries")
            conn.execute("DELETE FROM periods")
            conn.execute("DELETE FROM people")
            conn.executemany(
                "INSERT INTO people(name,color,sort_order) VALUES(?,?,?)",
                normalized_people,
            )
            person_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM people").fetchall()}
            conn.executemany(
                """INSERT INTO entries(day,person_id,note,created_at,updated_at)
                   VALUES(?,?,?,?,?)""",
                [(day, person_ids[person], note, created_at, updated_at)
                 for day, person, note, created_at, updated_at in normalized_entries],
            )
            conn.executemany(
                """INSERT INTO periods(start_day,end_day,kind,label,color,source,external_uid,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                normalized_periods,
            )
    except sqlite3.Error as exc:
        return jsonify({"error": f"Import fehlgeschlagen: {exc}"}), 400

    backup_db("after-full-import")
    return jsonify({
        "ok": True,
        "people": len(normalized_people),
        "entries": len(normalized_entries),
        "periods": len(normalized_periods),
    })


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
