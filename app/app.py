import csv
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import socket
import ipaddress
import time
from urllib.parse import urlencode, urlparse, urljoin
from datetime import datetime, date, timedelta
from functools import wraps
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from flask import (
    Flask, Response, jsonify, redirect, render_template, request,
    session, url_for, send_from_directory
)

import requests
import qrcode

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
APP_URL = os.getenv("APP_URL", "").strip().rstrip("/")
ICAL_EXPORT_NAME = os.getenv("ICAL_EXPORT_NAME", "Betreuungsplan").strip() or "Betreuungsplan"
PWA_ALLOWED_ORIGIN = os.getenv("PWA_ALLOWED_ORIGIN", "").strip().rstrip("/")
PWA_ALLOWED_ORIGINS_RAW = os.getenv("PWA_ALLOWED_ORIGINS", "").strip()
PWA_ALLOWED_ORIGINS = {
    value.strip().rstrip("/")
    for value in ([*PWA_ALLOWED_ORIGINS_RAW.split(","), PWA_ALLOWED_ORIGIN])
    if value.strip()
}

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


@app.after_request
def add_pwa_cors_headers(response):
    """Allow only the explicitly configured GitHub Pages PWA origin.

    Cloudflare Access should answer unauthenticated OPTIONS preflight requests at
    the edge. These headers are still required on the actual Flask responses.
    """
    if not PWA_ALLOWED_ORIGINS:
        return response
    origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if origin not in PWA_ALLOWED_ORIGINS:
        return response
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, CF-Access-Client-ID, CF-Access-Client-Secret"
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition, Content-Type"
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


backup_lock = threading.Lock()


class YearOverviewCell(Flowable):
    """Compact annual-plan cell with optional caregiver fill and period rail."""

    def __init__(self, width, height, entry=None, periods=None, continuation=None):
        super().__init__()
        self.width = width
        self.height = height
        self.entry = entry
        self.periods = periods or []
        self.continuation = continuation

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
        continuation_h = fill_h * 0.27 if self.continuation else 0
        continuation_gap = 0.55 if self.continuation and self.entry else 0
        entry_h = fill_h - continuation_h - continuation_gap if self.continuation else fill_h

        if self.entry:
            try:
                bg = colors.HexColor(self.entry.get("color") or "#ececec")
            except Exception:
                bg = colors.HexColor("#ececec")
            c.setFillColor(bg)
            c.roundRect(inset_x, inset_y, max(1, fill_w), max(1, entry_h), 2.0, stroke=0, fill=1)

            label = str(self.entry.get("person") or "")
            max_text_w = max(5, fill_w - 3)
            font_size = 4.8 if not self.continuation else 4.15
            min_font = 3.1 if self.continuation else 3.4
            while font_size > min_font and c.stringWidth(label, "Helvetica-Bold", font_size) > max_text_w:
                font_size -= 0.25
            if c.stringWidth(label, "Helvetica-Bold", font_size) > max_text_w:
                while len(label) > 2 and c.stringWidth(label + "…", "Helvetica-Bold", font_size) > max_text_w:
                    label = label[:-1]
                label += "…"
            c.setFont("Helvetica-Bold", font_size)
            c.setFillColor(colors.HexColor("#1e2524"))
            c.drawCentredString(inset_x + fill_w / 2, inset_y + (entry_h - font_size) / 2 + 0.7, label)

        if self.continuation:
            try:
                continuation_bg = colors.HexColor(self.continuation.get("color") or "#ececec")
            except Exception:
                continuation_bg = colors.HexColor("#ececec")
            continuation_y = self.height - inset_y - continuation_h
            c.setFillColor(continuation_bg)
            c.roundRect(inset_x, continuation_y, max(1, fill_w), max(1, continuation_h), 1.5, stroke=0, fill=1)
            continuation_text = str(self.continuation.get("continuation_text") or "").strip()
            continuation_label = str(self.continuation.get("person") or "")
            if continuation_text.startswith("bis "):
                continuation_label = f"{continuation_label} · {continuation_text}"
            max_text_w = max(5, fill_w - 2)
            continuation_font = 3.35
            while continuation_font > 2.45 and c.stringWidth(continuation_label, "Helvetica-Bold", continuation_font) > max_text_w:
                continuation_font -= 0.2
            if c.stringWidth(continuation_label, "Helvetica-Bold", continuation_font) > max_text_w:
                while len(continuation_label) > 3 and c.stringWidth(continuation_label + "…", "Helvetica-Bold", continuation_font) > max_text_w:
                    continuation_label = continuation_label[:-1]
                continuation_label += "…"
            c.setFont("Helvetica-Bold", continuation_font)
            c.setFillColor(colors.HexColor("#1e2524"))
            c.drawCentredString(inset_x + fill_w / 2, continuation_y + max(0.2, (continuation_h - continuation_font) / 2 + 0.25), continuation_label)

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
          sort_order INTEGER NOT NULL DEFAULT 100,
          ical_title TEXT NOT NULL DEFAULT '',
          calendar_token TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          day TEXT NOT NULL UNIQUE,
          end_day TEXT NOT NULL DEFAULT '',
          person_id INTEGER NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          all_day INTEGER NOT NULL DEFAULT 1,
          start_time TEXT NOT NULL DEFAULT '',
          end_time TEXT NOT NULL DEFAULT '',
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

        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS calendar_subscriptions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          url TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'holiday',
          color TEXT NOT NULL DEFAULT '#d65a6f',
          enabled INTEGER NOT NULL DEFAULT 1,
          last_sync_at TEXT NOT NULL DEFAULT '',
          last_status TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          action TEXT NOT NULL,
          entry_id INTEGER,
          snapshot TEXT NOT NULL DEFAULT '{}',
          before_snapshot TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
        """)
        history_columns = {r["name"] for r in conn.execute("PRAGMA table_info(history)").fetchall()}
        if "before_snapshot" not in history_columns:
            conn.execute("ALTER TABLE history ADD COLUMN before_snapshot TEXT NOT NULL DEFAULT '{}'")
        # Backwards-compatible migration for databases created before timed entries existed.
        entry_columns = {r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}
        if "all_day" not in entry_columns:
            conn.execute("ALTER TABLE entries ADD COLUMN all_day INTEGER NOT NULL DEFAULT 1")
        if "start_time" not in entry_columns:
            conn.execute("ALTER TABLE entries ADD COLUMN start_time TEXT NOT NULL DEFAULT ''")
        if "end_time" not in entry_columns:
            conn.execute("ALTER TABLE entries ADD COLUMN end_time TEXT NOT NULL DEFAULT ''")
        if "end_day" not in entry_columns:
            conn.execute("ALTER TABLE entries ADD COLUMN end_day TEXT NOT NULL DEFAULT ''")
        # Preserve every existing record. Old timed entries with an end time earlier
        # than the start time represented an overnight appointment ending next day.
        conn.execute("""
            UPDATE entries
               SET end_day = CASE
                   WHEN all_day=0 AND start_time<>'' AND end_time<>'' AND end_time < start_time
                     THEN date(day, '+1 day')
                   ELSE day
               END
             WHERE end_day='' OR end_day IS NULL
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_end_day ON entries(end_day)")
        people_columns = {r["name"] for r in conn.execute("PRAGMA table_info(people)").fetchall()}
        if "ical_title" not in people_columns:
            conn.execute("ALTER TABLE people ADD COLUMN ical_title TEXT NOT NULL DEFAULT ''")
        if "calendar_token" not in people_columns:
            conn.execute("ALTER TABLE people ADD COLUMN calendar_token TEXT NOT NULL DEFAULT ''")

        # Every person gets an independent revocable calendar token.
        for row in conn.execute("SELECT id,calendar_token FROM people").fetchall():
            if not row["calendar_token"]:
                conn.execute("UPDATE people SET calendar_token=? WHERE id=?", (secrets.token_urlsafe(32), row["id"]))

        existing = conn.execute("SELECT COUNT(*) AS c FROM people").fetchone()["c"]
        if existing == 0:
            conn.executemany(
                "INSERT INTO people(name,color,sort_order,calendar_token) VALUES(?,?,?,?)",
                [(name,color,order,secrets.token_urlsafe(32)) for name,color,order in DEFAULT_PEOPLE],
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


def _icon_response(filename):
    response = send_from_directory(app.static_folder, filename, mimetype="image/png")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/apple-touch-icon-v17.png")
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return _icon_response("apple-touch-icon-v17.png")


@app.get("/pwa-icon-192-v17.png")
def pwa_icon_192():
    return _icon_response("pwa-icon-192-v17.png")


@app.get("/pwa-icon-512-v17.png")
def pwa_icon_512():
    return _icon_response("pwa-icon-512-v17.png")


@app.get("/pwa-icon-maskable-512-v17.png")
def pwa_icon_maskable_512():
    return _icon_response("pwa-icon-maskable-512-v17.png")


@app.get("/favicon-v17.png")
@app.get("/favicon.ico")
def favicon_v17():
    return _icon_response("favicon-v17.png")


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
            {"src": "/pwa-icon-192-v17.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/pwa-icon-512-v17.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/pwa-icon-maskable-512-v17.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
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


def valid_hhmm(value):
    value = str(value or "").strip()
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except Exception:
        return None


def normalize_entry_timing(payload, allow_equal=False):
    raw_all_day = payload.get("all_day", True)
    if isinstance(raw_all_day, str):
        all_day = raw_all_day.strip().lower() not in {"0", "false", "no", "off"}
    else:
        all_day = bool(raw_all_day)
    if all_day:
        return 1, "", "", None

    start_time = valid_hhmm(payload.get("start_time"))
    end_time = valid_hhmm(payload.get("end_time"))
    if not start_time or not end_time:
        return None, None, None, "Von und Bis sind bei einem Termin mit Uhrzeit erforderlich"
    if end_time == start_time and not allow_equal:
        return None, None, None, "Von und Bis dürfen nicht gleich sein"
    return 0, start_time, end_time, None


def resolve_entry_end_day(day, all_day, start_time, end_time, raw_end_day=None):
    """Resolve the inclusive calendar date on which a timed entry ends.

    Missing end_day keeps backwards compatibility: an end time earlier than the
    start time means the following day. New clients send end_day explicitly and
    can therefore create arbitrary multi-day intervals.
    """
    if all_day:
        return day, None

    raw = str(raw_end_day or "").strip()
    if raw:
        end_day = valid_iso_day(raw)
        if not end_day:
            return None, "Bis-Datum ist ungültig"
    else:
        end_obj = date.fromisoformat(day)
        if end_time < start_time:
            end_obj += timedelta(days=1)
        end_day = end_obj.isoformat()

    if end_day < day:
        return None, "Bis-Datum darf nicht vor dem Von-Datum liegen"
    if end_day == day and end_time <= start_time:
        return None, "Bis muss nach Von liegen"
    if (date.fromisoformat(end_day) - date.fromisoformat(day)).days > 3660:
        return None, "Ein Betreuungseintrag darf maximal zehn Jahre umfassen"
    return end_day, None


def entry_effective_end_day(row):
    day = str(row.get("day") or "")
    explicit = valid_iso_day(str(row.get("end_day") or ""))
    if explicit:
        return explicit
    if not row.get("all_day") and row.get("start_time") and row.get("end_time") and row["end_time"] < row["start_time"]:
        return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    return day


def entry_rows(where="", params=()):
    query = """
      SELECT e.id, e.day, e.end_day, e.note, e.all_day, e.start_time, e.end_time,
             e.created_at, e.updated_at,
             p.id AS person_id, p.name AS person, p.color AS color,
             p.ical_title AS ical_title
      FROM entries e
      JOIN people p ON p.id = e.person_id
    """
    if where:
        query += " WHERE " + where
    query += " ORDER BY e.day ASC"
    with db() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def public_app_url():
    """Public HTTPS base URL used for shareable calendar links."""
    if APP_URL:
        parsed = urlparse(APP_URL)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise RuntimeError("APP_URL muss eine vollständige HTTPS-URL sein, z. B. https://betreuung.example.ch")
        return APP_URL
    # Reverse-proxy friendly fallback: calendar subscription links are always HTTPS.
    # APP_URL is still recommended so links never depend on the incoming Host header.
    return f"https://{request.host.split(':')[0]}"


def ical_feed_url(person_id=None, person_token=None):
    base = public_app_url() + "/calendar.ics"
    if not ICAL_TOKEN:
        return None
    if person_id is not None and person_token:
        params = {"person_id": int(person_id), "token": person_token}
    else:
        params = {"token": ICAL_TOKEN}
    return base + "?" + urlencode(params)


def qr_png_response(value):
    image = qrcode.make(value)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    response = Response(buf.getvalue(), mimetype="image/png")
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@app.after_request
def harden_calendar_response(response):
    if request.path == "/calendar.ics":
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def get_setting(key, default=""):
    with db() as conn:
        row=conn.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return row["value"] if row else default

def set_setting(key, value):
    with db() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))

HISTORY_ENTRY_FIELDS = ("day", "end_day", "person_id", "person", "note", "all_day", "start_time", "end_time")

def history_add(action, snapshot, entry_id=None, before_snapshot=None):
    with db() as conn:
        conn.execute(
            "INSERT INTO history(action,entry_id,snapshot,before_snapshot,created_at) VALUES(?,?,?,?,?)",
            (
                action,
                entry_id,
                json.dumps(snapshot or {}, ensure_ascii=False),
                json.dumps(before_snapshot or {}, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 100)")

def entry_snapshot_with_conn(conn, entry_id):
    r=conn.execute(
        "SELECT e.id,e.day,e.end_day,e.person_id,e.note,e.all_day,e.start_time,e.end_time,p.name AS person "
        "FROM entries e JOIN people p ON p.id=e.person_id WHERE e.id=?",
        (entry_id,),
    ).fetchone()
    return dict(r) if r else None

def entry_snapshot(entry_id):
    with db() as conn:
        return entry_snapshot_with_conn(conn, entry_id)

def entry_snapshots_differ(before, after):
    before = before or {}
    after = after or {}
    return any(before.get(key) != after.get(key) for key in HISTORY_ENTRY_FIELDS)


@app.get("/api/history")
@login_required
def api_history():
    with db() as conn:
        rows = conn.execute("SELECT id,action,entry_id,snapshot,before_snapshot,created_at FROM history ORDER BY id DESC LIMIT 20").fetchall()
    result=[]
    for row in rows:
        try:
            snapshot=json.loads(row["snapshot"] or "{}")
            if not isinstance(snapshot, dict): snapshot={}
        except Exception:
            snapshot={}
        try:
            before_snapshot=json.loads(row["before_snapshot"] or "{}")
            if not isinstance(before_snapshot, dict): before_snapshot={}
        except Exception:
            before_snapshot={}
        result.append({
            "id":row["id"],
            "action":row["action"],
            "entry_id":row["entry_id"],
            "snapshot":snapshot,
            "after":snapshot,
            "before":before_snapshot,
            "created_at":row["created_at"],
        })
    return jsonify(result)


@app.post("/api/history/<int:history_id>/restore")
@login_required
def api_history_restore(history_id):
    with db() as conn:
        row=conn.execute("SELECT * FROM history WHERE id=?",(history_id,)).fetchone()
        if not row:
            return jsonify({"error":"Änderung nicht gefunden"}),404
        try:
            snap=json.loads(row["snapshot"] or "{}")
        except Exception:
            return jsonify({"error":"Gespeicherter Eintrag ist ungültig"}),400
        day=valid_iso_day(str(snap.get("day", "")))
        if not day:
            return jsonify({"error":"Gespeichertes Datum ist ungültig"}),400
        occupied=conn.execute("SELECT id FROM entries WHERE day=?",(day,)).fetchone()
        if occupied:
            return jsonify({"error":"Für dieses Datum existiert bereits ein Betreuungseintrag"}),409
        person_id=snap.get("person_id")
        person=conn.execute("SELECT id FROM people WHERE id=?",(person_id,)).fetchone() if person_id else None
        if not person and snap.get("person"):
            person=conn.execute("SELECT id FROM people WHERE name=?",(str(snap.get("person")),)).fetchone()
        if not person:
            return jsonify({"error":"Die Betreuungsperson existiert nicht mehr"}),409
        now=datetime.now().isoformat(timespec="seconds")
        snap_all_day=1 if snap.get("all_day",1) else 0
        snap_start=str(snap.get("start_time", ""))
        snap_end=str(snap.get("end_time", ""))
        restored_end_day, end_error = resolve_entry_end_day(day, snap_all_day, snap_start, snap_end, snap.get("end_day"))
        if end_error:
            return jsonify({"error":f"Gespeicherter Eintrag ist ungültig: {end_error}"}),400
        cur=conn.execute("""INSERT INTO entries(day,end_day,person_id,note,all_day,start_time,end_time,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (day,restored_end_day,person["id"],str(snap.get("note", "")),snap_all_day,snap_start,snap_end,now,now))
        restored_id=cur.lastrowid
    history_add("restored", entry_snapshot(restored_id), restored_id)
    backup_db("history-restore")
    return jsonify({"ok":True,"id":restored_id})

@app.get("/api/config")
@login_required
def api_config():
    person_urls = []
    if ICAL_TOKEN:
        with db() as conn:
            rows = conn.execute(
                "SELECT id,name,color,ical_title,calendar_token FROM people ORDER BY sort_order,name"
            ).fetchall()
        person_urls = [
            {"id": r["id"], "name": r["name"], "color": r["color"], "ical_title": r["ical_title"], "url": ical_feed_url(r["id"], r["calendar_token"]), "qr_url": f"/api/people/{r["id"]}/calendar-qr.png"}
            for r in rows
        ]
    return jsonify({
        "title": APP_TITLE,
        "ical_enabled": bool(ICAL_TOKEN),
        "ical_url": ical_feed_url() if ICAL_TOKEN else None,
        "ical_qr_url": "/api/calendar-qr.png" if ICAL_TOKEN else None,
        "ical_person_urls": person_urls,
        "data_file": str(DB_PATH),
        "backup_dir": str(BACKUP_DIR),
        "auth_enabled": AUTH_ENABLED,
        "ical_title_template": get_setting("ical_title_template", "{person}"),
    })

@app.put("/api/config")
@login_required
def api_config_update():
    payload=request.get_json(force=True)
    template=str(payload.get("ical_title_template", "{person}")).strip() or "{person}"
    if len(template)>80 or "{person}" not in template:
        return jsonify({"error":"Kalendertitel muss {person} enthalten und maximal 80 Zeichen lang sein"}),400
    set_setting("ical_title_template",template)
    return jsonify({"ok":True,"ical_title_template":template})


@app.get("/api/people")
@login_required
def api_people():
    with db() as conn:
        rows = conn.execute(
            "SELECT id,name,color,sort_order,ical_title FROM people ORDER BY sort_order,name"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/people")
@login_required
def api_people_add():
    payload = request.get_json(force=True)
    name = str(payload.get("name", "")).strip()
    color = str(payload.get("color", "#ececec")).strip()
    ical_title = str(payload.get("ical_title", "")).strip()
    if len(ical_title) > 80:
        return jsonify({"error": "Persönlicher Kalendertitel darf maximal 80 Zeichen lang sein"}), 400
    if not name:
        return jsonify({"error": "Name fehlt"}), 400
    if not color.startswith("#") or len(color) not in (4, 7):
        color = "#ececec"
    try:
        with db() as conn:
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) AS m FROM people").fetchone()["m"]
            cur = conn.execute(
                "INSERT INTO people(name,color,sort_order,ical_title,calendar_token) VALUES(?,?,?,?,?)",
                (name, color, max_order + 10, ical_title, secrets.token_urlsafe(32)),
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
    ical_title = str(payload.get("ical_title", "")).strip()
    if len(ical_title) > 80:
        return jsonify({"error": "Persönlicher Kalendertitel darf maximal 80 Zeichen lang sein"}), 400
    if not name:
        return jsonify({"error": "Name fehlt"}), 400
    try:
        with db() as conn:
            conn.execute(
                "UPDATE people SET name=?,color=?,ical_title=? WHERE id=?",
                (name, color, ical_title, person_id),
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


@app.post("/api/people/<int:person_id>/calendar-token/reset")
@login_required
def api_person_calendar_token_reset(person_id):
    new_token = secrets.token_urlsafe(32)
    with db() as conn:
        row = conn.execute("SELECT id FROM people WHERE id=?", (person_id,)).fetchone()
        if not row:
            return jsonify({"error": "Person nicht gefunden"}), 404
        conn.execute("UPDATE people SET calendar_token=? WHERE id=?", (new_token, person_id))
    backup_db("calendar-token-reset")
    return jsonify({"ok": True})


@app.get("/api/people/<int:person_id>/calendar-qr.png")
@login_required
def api_person_calendar_qr(person_id):
    with db() as conn:
        row = conn.execute("SELECT calendar_token FROM people WHERE id=?", (person_id,)).fetchone()
    if not row or not ICAL_TOKEN:
        return Response("Not found", status=404)
    return qr_png_response(ical_feed_url(person_id, row["calendar_token"]))


@app.get("/api/calendar-qr.png")
@login_required
def api_calendar_qr():
    if not ICAL_TOKEN:
        return Response("Not found", status=404)
    return qr_png_response(ical_feed_url())


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
    all_day, start_time, end_time, timing_error = normalize_entry_timing(payload, allow_equal=True)
    if not day or not person_id:
        return jsonify({"error": "Datum und Betreuung sind erforderlich"}), 400
    if timing_error:
        return jsonify({"error": timing_error}), 400
    end_day, end_day_error = resolve_entry_end_day(day, all_day, start_time, end_time, payload.get("end_day"))
    if end_day_error:
        return jsonify({"error": end_day_error}), 400

    now = datetime.now().isoformat(timespec="seconds")
    try:
        before_snapshot = None
        with db() as conn:
            existing = conn.execute("SELECT id FROM entries WHERE day=?", (day,)).fetchone()
            if existing:
                before_snapshot = entry_snapshot_with_conn(conn, existing["id"])
                conn.execute(
                    "UPDATE entries SET end_day=?,person_id=?,note=?,all_day=?,start_time=?,end_time=?,updated_at=? WHERE id=?",
                    (end_day, person_id, note, all_day, start_time, end_time, now, existing["id"]),
                )
                entry_id = existing["id"]
                status = 200
            else:
                cur = conn.execute(
                    """INSERT INTO entries(day,end_day,person_id,note,all_day,start_time,end_time,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (day, end_day, person_id, note, all_day, start_time, end_time, now, now),
                )
                entry_id = cur.lastrowid
                status = 201
        after_snapshot = entry_snapshot(entry_id)
        if status == 201:
            history_add("created", after_snapshot, entry_id)
        elif entry_snapshots_differ(before_snapshot, after_snapshot):
            history_add("updated", after_snapshot, entry_id, before_snapshot=before_snapshot)
        backup_db("entry")
        return jsonify({"id": entry_id, "day": day, "end_day": end_day}), status
    except sqlite3.IntegrityError as exc:
        return jsonify({"error": str(exc)}), 409


@app.put("/api/entries/<int:entry_id>")
@login_required
def api_entries_update(entry_id):
    payload = request.get_json(force=True)
    day = valid_iso_day(str(payload.get("day", "")))
    person_id = payload.get("person_id")
    note = str(payload.get("note", "")).strip()
    all_day, start_time, end_time, timing_error = normalize_entry_timing(payload, allow_equal=True)
    if not day or not person_id:
        return jsonify({"error": "Datum und Betreuung sind erforderlich"}), 400
    if timing_error:
        return jsonify({"error": timing_error}), 400
    end_day, end_day_error = resolve_entry_end_day(day, all_day, start_time, end_time, payload.get("end_day"))
    if end_day_error:
        return jsonify({"error": end_day_error}), 400

    try:
        with db() as conn:
            before_snapshot = entry_snapshot_with_conn(conn, entry_id)
            if not before_snapshot:
                return jsonify({"error": "Eintrag nicht gefunden"}), 404
            conn.execute(
                """UPDATE entries
                   SET day=?,end_day=?,person_id=?,note=?,all_day=?,start_time=?,end_time=?,updated_at=?
                   WHERE id=?""",
                (day, end_day, person_id, note, all_day, start_time, end_time, datetime.now().isoformat(timespec="seconds"), entry_id),
            )
        after_snapshot = entry_snapshot(entry_id)
        if entry_snapshots_differ(before_snapshot, after_snapshot):
            history_add("updated", after_snapshot, entry_id, before_snapshot=before_snapshot)
        backup_db("entry-update")
        return jsonify({"ok": True, "end_day": end_day})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Für dieses Datum gibt es bereits einen Eintrag"}), 409


@app.delete("/api/entries/<int:entry_id>")
@login_required
def api_entries_delete(entry_id):
    snap=entry_snapshot(entry_id)
    with db() as conn:
        conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
    if snap: history_add("deleted", snap, entry_id)
    backup_db("entry-delete")
    return jsonify({"ok": True})


def parse_batch_payload():
    payload = request.get_json(force=True)
    start_day = valid_iso_day(str(payload.get("start_day", "")))
    end_day = valid_iso_day(str(payload.get("end_day", "")))
    person_id = payload.get("person_id")
    note = str(payload.get("note", "")).strip()
    all_day, start_time, end_time, timing_error = normalize_entry_timing(payload)
    if timing_error:
        return None, timing_error

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
        "all_day": all_day,
        "start_time": start_time,
        "end_time": end_time,
        "days": days,
        "skip_vacations": bool(payload.get("skip_vacations", False)),
        "skip_holidays": bool(payload.get("skip_holidays", False)),
    }, None


def batch_preview_data(batch):
    days=batch["days"]
    occupied=[]; excluded={}
    if days:
        placeholders=",".join("?" for _ in days)
        with db() as conn:
            rows=conn.execute(f"SELECT e.day,p.name AS person FROM entries e JOIN people p ON p.id=e.person_id WHERE e.day IN ({placeholders}) ORDER BY e.day",tuple(days)).fetchall()
            occupied=[dict(r) for r in rows]
            if batch["skip_vacations"] or batch["skip_holidays"]:
                kinds=[]
                if batch["skip_vacations"]: kinds.append("vacation")
                if batch["skip_holidays"]: kinds.append("holiday")
                ph=",".join("?" for _ in kinds)
                prs=conn.execute(f"SELECT start_day,end_day,kind FROM periods WHERE kind IN ({ph})",tuple(kinds)).fetchall()
                for d in days:
                    hits={r["kind"] for r in prs if r["start_day"]<=d<=r["end_day"]}
                    if hits: excluded[d]=hits
    occupied_days={r["day"] for r in occupied}
    create_days=[d for d in days if d not in occupied_days and d not in excluded]
    return {"matched_count":len(days),"create_count":len(create_days),"skipped_count":len(days)-len(create_days),"occupied":occupied,"vacation_count":sum("vacation" in x for x in excluded.values()),"holiday_count":sum("holiday" in x for x in excluded.values()),"create_days":create_days,"first_day":days[0] if days else None,"last_day":days[-1] if days else None}


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

    preview=batch_preview_data(batch)
    now = datetime.now().isoformat(timespec="seconds")
    created = 0
    with db() as conn:
        for day in preview["create_days"]:
            entry_end_day, _ = resolve_entry_end_day(day, batch["all_day"], batch["start_time"], batch["end_time"], None)
            cur = conn.execute(
                """INSERT OR IGNORE INTO entries(day,end_day,person_id,note,all_day,start_time,end_time,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (day, entry_end_day, batch["person_id"], batch["note"], batch["all_day"], batch["start_time"], batch["end_time"], now, now),
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
        "vacation_count": preview["vacation_count"],
        "holiday_count": preview["holiday_count"],
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


def _subscription_url_is_safe(url):
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False, "Ungültige URL"
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return False, "Nur HTTP/HTTPS-URLs sind erlaubt"
    if parsed.username or parsed.password:
        return False, "Benutzername/Passwort in der URL werden nicht unterstützt"
    if parsed.scheme == "http" and os.getenv("ALLOW_HTTP_CALENDAR_SUBSCRIPTIONS", "false").lower() not in {"1","true","yes","on"}:
        return False, "Kalender-Abos müssen HTTPS verwenden"
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return False, "Interne/private Zieladressen sind nicht erlaubt"
    except Exception:
        return False, "Kalender-Host konnte nicht aufgelöst werden"
    return True, ""


def fetch_subscription_ics(url):
    current = url
    for _ in range(4):
        safe, error = _subscription_url_is_safe(current)
        if not safe:
            raise ValueError(error)
        response = requests.get(current, timeout=(5, 20), allow_redirects=False, headers={"User-Agent": "Betreuungsplan/30", "Accept": "text/calendar,text/plain;q=0.9,*/*;q=0.2"})
        if response.status_code in {301,302,303,307,308}:
            location = response.headers.get("Location")
            if not location:
                raise ValueError("Kalender-Weiterleitung ohne Ziel")
            current = urljoin(current, location)
            continue
        if response.status_code < 200 or response.status_code >= 300:
            raise ValueError(f"Kalender-Server antwortet mit HTTP {response.status_code}")
        if len(response.content) > 5 * 1024 * 1024:
            raise ValueError("Kalender ist größer als 5 MB")
        try:
            return response.content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return response.content.decode("latin-1")
    raise ValueError("Zu viele Weiterleitungen")


def sync_calendar_subscription(subscription_id):
    with db() as conn:
        sub = conn.execute("SELECT * FROM calendar_subscriptions WHERE id=?", (subscription_id,)).fetchone()
    if not sub:
        raise ValueError("Kalender-Abo nicht gefunden")
    if not sub["enabled"]:
        return {"imported": 0, "skipped": 0, "disabled": True}
    now = datetime.now().isoformat(timespec="seconds")
    try:
        raw = fetch_subscription_ics(sub["url"])
        imported = 0
        skipped = 0
        keep_uids = []
        source = f"subscription:{subscription_id}"
        with db() as conn:
            for event in parse_ics_events(raw):
                occurrences = expand_ics_dates(event)
                if not occurrences:
                    skipped += 1
                    continue
                summary = ics_unescape(event.get("SUMMARY", ("SUMMARY", "Kalendertermin"))[1]).strip() or "Kalendertermin"
                uid = ics_unescape(event.get("UID", ("UID", ""))[1]).strip()
                for start_obj, end_obj, recurrence_year in occurrences:
                    base_uid = uid if uid else f"{start_obj.isoformat()}:{end_obj.isoformat()}:{summary}"
                    external_uid = f"sub:{subscription_id}:" + base_uid + (f":{recurrence_year}" if recurrence_year else "")
                    keep_uids.append(external_uid)
                    conn.execute(
                        """INSERT INTO periods(start_day,end_day,kind,label,color,source,external_uid,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(external_uid) DO UPDATE SET
                             start_day=excluded.start_day,end_day=excluded.end_day,kind=excluded.kind,
                             label=excluded.label,color=excluded.color,source=excluded.source,updated_at=excluded.updated_at""",
                        (start_obj.isoformat(), end_obj.isoformat(), sub["kind"], summary, sub["color"], source, external_uid, now, now),
                    )
                    imported += 1
            if keep_uids:
                placeholders = ",".join("?" for _ in keep_uids)
                conn.execute(f"DELETE FROM periods WHERE source=? AND external_uid NOT IN ({placeholders})", (source, *keep_uids))
            else:
                conn.execute("DELETE FROM periods WHERE source=?", (source,))
            conn.execute("UPDATE calendar_subscriptions SET last_sync_at=?,last_status=?,updated_at=? WHERE id=?", (now, f"OK · {imported} Termine", now, subscription_id))
        return {"imported": imported, "skipped": skipped}
    except Exception as exc:
        with db() as conn:
            conn.execute("UPDATE calendar_subscriptions SET last_sync_at=?,last_status=?,updated_at=? WHERE id=?", (now, f"Fehler · {str(exc)[:180]}", now, subscription_id))
        raise


def subscription_rows():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM calendar_subscriptions ORDER BY name,id").fetchall()]


@app.get("/api/calendar-subscriptions")
@login_required
def api_calendar_subscriptions():
    return jsonify(subscription_rows())


@app.post("/api/calendar-subscriptions")
@login_required
def api_calendar_subscriptions_add():
    payload = request.get_json(force=True)
    name = str(payload.get("name", "")).strip()
    url = str(payload.get("url", "")).strip()
    kind = str(payload.get("kind", "holiday")).strip() or "holiday"
    if kind not in {"holiday","vacation","other"}:
        kind = "other"
    default_color = "#d65a6f" if kind == "holiday" else "#f2a65a" if kind == "vacation" else "#80a4c2"
    color = valid_color(payload.get("color"), default_color)
    if not name or not url:
        return jsonify({"error": "Name und Kalender-URL sind erforderlich"}), 400
    safe, error = _subscription_url_is_safe(url)
    if not safe:
        return jsonify({"error": error}), 400
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        cur = conn.execute("INSERT INTO calendar_subscriptions(name,url,kind,color,enabled,last_sync_at,last_status,created_at,updated_at) VALUES(?,?,?,?,1,'','Noch nicht synchronisiert',?,?)", (name,url,kind,color,now,now))
        sub_id = cur.lastrowid
    try:
        result = sync_calendar_subscription(sub_id)
    except Exception as exc:
        result = {"warning": str(exc)}
    backup_db("calendar-subscription-add")
    return jsonify({"id": sub_id, **result}), 201


@app.put("/api/calendar-subscriptions/<int:subscription_id>")
@login_required
def api_calendar_subscriptions_update(subscription_id):
    payload = request.get_json(force=True)
    with db() as conn:
        current = conn.execute("SELECT * FROM calendar_subscriptions WHERE id=?", (subscription_id,)).fetchone()
    if not current:
        return jsonify({"error": "Kalender-Abo nicht gefunden"}), 404
    name = str(payload.get("name", current["name"])).strip()
    url = str(payload.get("url", current["url"])).strip()
    kind = str(payload.get("kind", current["kind"])).strip()
    color = valid_color(payload.get("color", current["color"]), current["color"])
    enabled = 1 if bool(payload.get("enabled", current["enabled"])) else 0
    safe, error = _subscription_url_is_safe(url)
    if not safe:
        return jsonify({"error": error}), 400
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        conn.execute("UPDATE calendar_subscriptions SET name=?,url=?,kind=?,color=?,enabled=?,updated_at=? WHERE id=?", (name,url,kind,color,enabled,now,subscription_id))
    if enabled:
        try: sync_calendar_subscription(subscription_id)
        except Exception: pass
    else:
        with db() as conn: conn.execute("DELETE FROM periods WHERE source=?", (f"subscription:{subscription_id}",))
    backup_db("calendar-subscription-update")
    return jsonify({"ok": True})


@app.post("/api/calendar-subscriptions/<int:subscription_id>/sync")
@login_required
def api_calendar_subscriptions_sync(subscription_id):
    try:
        result = sync_calendar_subscription(subscription_id)
        backup_db("calendar-subscription-sync")
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.delete("/api/calendar-subscriptions/<int:subscription_id>")
@login_required
def api_calendar_subscriptions_delete(subscription_id):
    with db() as conn:
        conn.execute("DELETE FROM periods WHERE source=?", (f"subscription:{subscription_id}",))
        conn.execute("DELETE FROM calendar_subscriptions WHERE id=?", (subscription_id,))
    backup_db("calendar-subscription-delete")
    return jsonify({"ok": True})


def sync_all_calendar_subscriptions():
    for sub in subscription_rows():
        if sub["enabled"]:
            try: sync_calendar_subscription(sub["id"])
            except Exception: pass


def calendar_subscription_worker():
    # Delay startup so the web process becomes responsive immediately.
    time.sleep(15)
    interval = max(1, int(os.getenv("SUBSCRIPTION_SYNC_HOURS", "12"))) * 3600
    while True:
        sync_all_calendar_subscriptions()
        time.sleep(interval)


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
    raw_months = request.args.getlist("month")
    months = sorted({int(m) for m in raw_months if m.isdigit() and 1 <= int(m) <= 12})
    rows = filtered_entry_rows(year, people, search)
    if months and year:
        prefixes = tuple(f"{year}-{m:02d}-" for m in months)
        rows = [r for r in rows if r["day"].startswith(prefixes)]

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Datum", "Bis-Datum", "Betreuung", "Ganztägig", "Von", "Bis", "Bemerkung"])
    for r in rows:
        w.writerow([r["day"], entry_effective_end_day(r), r["person"], "Ja" if r["all_day"] else "Nein", r["start_time"], r["end_time"], r["note"]])

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
        Paragraph("Zeit", header_style),
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
            Paragraph(
                "Ganztägig" if r["all_day"] else
                (f"{r['start_time']}–{r['end_time']}" if entry_effective_end_day(r) == r["day"]
                 else f"{r['start_time']} → {date.fromisoformat(entry_effective_end_day(r)).strftime('%d.%m.%Y')} {r['end_time']}"),
                cell_style
            ),
            Paragraph(xml_escape(r["note"] or ""), cell_style),
        ])
        try:
            row_colors.append((idx, colors.HexColor(r["color"])))
        except Exception:
            row_colors.append((idx, colors.HexColor("#ececec")))

    table = Table(
        data,
        colWidths=[27 * mm, 14 * mm, 38 * mm, 28 * mm, 79 * mm],
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

    raw_months = request.args.getlist("month")
    selected_months = sorted({int(m) for m in raw_months if m.isdigit() and 1 <= int(m) <= 12})

    month_names = ["Januar", "Februar", "März", "April", "Mai", "Juni",
                   "Juli", "August", "September", "Oktober", "November", "Dezember"]
    month_indices = selected_months if selected_months else list(range(1, 13))
    single_month = len(month_indices) == 1

    range_start = date(year, 1, 1).isoformat()
    range_end = date(year, 12, 31).isoformat()
    source_entries = entry_rows("e.day <= ? AND COALESCE(NULLIF(e.end_day,''), e.day) >= ?", (range_end, range_start))
    year_entries = [row for row in source_entries if row["day"].startswith(f"{year}-")]
    if selected_months:
        prefixes = tuple(f"{year}-{m:02d}-" for m in month_indices)
        display_entries = [row for row in year_entries if row["day"].startswith(prefixes)]
    else:
        display_entries = year_entries
    by_day = {row["day"]: row for row in display_entries}
    continuation_by_day = {}
    selected_month_set = set(month_indices)
    for row in source_entries:
        if row.get("all_day") or not row.get("start_time") or not row.get("end_time"):
            continue
        final_day = date.fromisoformat(entry_effective_end_day(row))
        current = date.fromisoformat(row["day"]) + timedelta(days=1)
        while current <= final_day:
            if current.year == year and current.month in selected_month_set:
                continuation = dict(row)
                continuation["continuation_text"] = f"bis {row['end_time']}" if current == final_day else "ganzer Tag"
                continuation_by_day[current.isoformat()] = continuation
            current += timedelta(days=1)

    year_periods = period_rows(str(year))

    display_periods = []
    periods_by_day = {}
    for period in year_periods:
        period_start = date.fromisoformat(period["start_day"])
        period_end = date.fromisoformat(period["end_day"])
        if period_end < date(year, 1, 1) or period_start > date(year, 12, 31):
            continue
        period_used = False
        start_obj = max(period_start, date(year, 1, 1))
        end_obj = min(period_end, date(year, 12, 31))
        current = start_obj
        while current <= end_obj:
            if current.month in selected_month_set:
                periods_by_day.setdefault(current.isoformat(), []).append(period)
                period_used = True
            current += timedelta(days=1)
        if period_used:
            display_periods.append(period)

    output = io.BytesIO()
    page_size = A4 if single_month else landscape(A4)
    margin = 10 * mm if single_month else 8 * mm
    plan_title = (f"Monatsplan {month_names[month_indices[0] - 1]} {year}" if single_month else (f"Jahresplan {year}" if len(month_indices)==12 else f"Monatsauswahl {year} · {len(month_indices)} Monate"))
    doc = SimpleDocTemplate(
        output,
        pagesize=page_size,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=7 * mm,
        bottomMargin=7 * mm,
        title=plan_title,
        author=APP_TITLE,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "YearTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=10 if single_month else 9.2,
        leading=11 if single_month else 10.2,
        textColor=colors.HexColor("#1e2524"),
        spaceAfter=1.2 * mm if single_month else 0.8 * mm,
    )
    head_style = ParagraphStyle(
        "YearHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=6.4 if single_month else 5.0,
        leading=6.8 if single_month else 5.4,
        alignment=1,
        textColor=colors.HexColor("#1e2524"),
    )

    usable_w = page_size[0] - doc.leftMargin - doc.rightMargin
    day_col_w = 10 * mm if single_month else 6.2 * mm
    month_w = (usable_w - 2 * day_col_w) / len(month_indices)
    header_h = 6 * mm if single_month else 4.5 * mm
    day_h = 5.8 * mm if single_month else 4.1 * mm

    data = [[Paragraph("Tag", head_style)] +
            [Paragraph(month_names[m - 1], head_style) for m in month_indices] +
            [Paragraph("Tag", head_style)]]
    last_col = len(month_indices) + 1
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dfe4e1")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f7f6")),
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#fafbfa")),
        ("BACKGROUND", (last_col, 1), (last_col, -1), colors.HexColor("#fafbfa")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (last_col, 1), (last_col, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (0, -1), 6 if single_month else 5.1),
        ("FONTSIZE", (last_col, 1), (last_col, -1), 6 if single_month else 5.1),
    ]

    for day_num in range(1, 32):
        row = [str(day_num)]
        pdf_row = day_num
        for col_pos, month_idx in enumerate(month_indices, start=1):
            try:
                d = date(year, month_idx, day_num)
                valid = True
            except ValueError:
                valid = False

            if not valid:
                row.append("")
                style_cmds.append(("BACKGROUND", (col_pos, pdf_row), (col_pos, pdf_row), colors.HexColor("#f1f2f1")))
                continue

            iso = d.isoformat()
            if d.weekday() >= 5:
                style_cmds.append(("BACKGROUND", (col_pos, pdf_row), (col_pos, pdf_row), colors.HexColor("#fff2b9")))
            row.append(YearOverviewCell(
                month_w,
                day_h,
                by_day.get(iso),
                periods_by_day.get(iso, []),
                continuation_by_day.get(iso),
            ))
        row.append(str(day_num))
        data.append(row)

    year_table = Table(
        data,
        colWidths=[day_col_w] + [month_w] * len(month_indices) + [day_col_w],
        rowHeights=[header_h] + [day_h] * 31,
        hAlign="LEFT",
    )
    year_table.setStyle(TableStyle(style_cmds))

    legend_items = []
    used_people = []
    seen_people = set()
    for entry in list(display_entries) + list(continuation_by_day.values()):
        key = (entry["person"], entry["color"])
        if key not in seen_people:
            seen_people.add(key)
            used_people.append(key)
    legend_items.extend(used_people)
    legend_items.append(("Wochenende", "#fff2b9"))
    if continuation_by_day:
        legend_items.append(("Fort. = mehrtägiger Eintrag", "#e4efeb"))

    kind_names = {"vacation": "Ferien", "holiday": "Feiertage", "other": "Markierung"}
    seen_period_legend = set()
    for period in display_periods:
        key = (kind_names.get(period.get("kind"), period.get("label") or "Markierung"), period.get("color") or "#80a4c2")
        if key not in seen_period_legend:
            seen_period_legend.add(key)
            legend_items.append(key)

    legend_rows = []
    per_row = 3 if single_month else 7
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

    story = [Paragraph(plan_title, title_style), year_table, Spacer(1, 0.8 * mm)]
    if legend:
        story.append(legend)

    def footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 6)
        canvas.setFillColor(colors.HexColor("#68716f"))
        canvas.drawString(margin, 3.2 * mm, APP_TITLE)
        canvas.drawRightString(page_size[0] - margin, 3.2 * mm, plan_title)
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pdf_bytes = output.getvalue()
    output.close()
    filename = (f"monatsplan-{year}-{month_indices[0]:02d}.pdf" if single_month else (year_export_filename(year) if len(month_indices)==12 else f"jahresplan-{year}-{len(month_indices)}-monate.pdf"))
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
            raw_all_day = (row.get("Ganztägig") or row.get("all_day") or "").strip().lower()
            start_time = valid_hhmm(row.get("Von") or row.get("start_time")) or ""
            end_time = valid_hhmm(row.get("Bis") or row.get("end_time")) or ""
            csv_end_day_raw = (row.get("Bis-Datum") or row.get("end_day") or "").strip()
            if raw_all_day:
                all_day = 0 if raw_all_day in {"nein", "no", "0", "false", "off"} else 1
            else:
                all_day = 0 if start_time and end_time else 1
            if not all_day and (not start_time or not end_time):
                continue
            if all_day:
                start_time = end_time = ""
            if not day or not person_name:
                continue
            csv_end_day, end_error = resolve_entry_end_day(day, all_day, start_time, end_time, csv_end_day_raw)
            if end_error:
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
                """INSERT INTO entries(day,end_day,person_id,note,all_day,start_time,end_time,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(day) DO UPDATE SET
                     end_day=excluded.end_day,
                     person_id=excluded.person_id,
                     note=excluded.note,
                     all_day=excluded.all_day,
                     start_time=excluded.start_time,
                     end_time=excluded.end_time,
                     updated_at=excluded.updated_at""",
                (day, csv_end_day, person_id, note, all_day, start_time, end_time, now, now),
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
            "SELECT name,color,sort_order,ical_title,calendar_token FROM people ORDER BY sort_order,name"
        ).fetchall()]
        entries = [dict(r) for r in conn.execute(
            """SELECT e.day,e.end_day,p.name AS person,e.note,e.all_day,e.start_time,e.end_time,e.created_at,e.updated_at
               FROM entries e JOIN people p ON p.id=e.person_id
               ORDER BY e.day"""
        ).fetchall()]
        periods = [dict(r) for r in conn.execute(
            """SELECT start_day,end_day,kind,label,color,source,external_uid,created_at,updated_at
               FROM periods WHERE source NOT LIKE 'subscription:%' ORDER BY start_day,end_day,label"""
        ).fetchall()]
        subscriptions = [dict(r) for r in conn.execute(
            """SELECT name,url,kind,color,enabled,last_sync_at,last_status,created_at,updated_at
               FROM calendar_subscriptions ORDER BY name,id"""
        ).fetchall()]

    payload = {
        "format": "betreuungsplan-backup",
        "version": 3,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "app_title": APP_TITLE,
        "people": people,
        "entries": entries,
        "periods": periods,
        "calendar_subscriptions": subscriptions,
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
    if payload.get("version") not in {1, 2, 3}:
        return jsonify({"error": f"Nicht unterstützte Backup-Version: {payload.get('version')}"}), 400

    people = payload.get("people")
    entries = payload.get("entries")
    periods = payload.get("periods")
    subscriptions = payload.get("calendar_subscriptions", [])
    if not isinstance(people, list) or not isinstance(entries, list) or not isinstance(periods, list) or not isinstance(subscriptions, list):
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
        normalized_people.append((name, valid_color(item.get("color"), "#ececec"), sort_order, str(item.get("ical_title", "")).strip()[:80], str(item.get("calendar_token") or secrets.token_urlsafe(32))))

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
        timing_payload = {
            "all_day": item.get("all_day", True),
            "start_time": item.get("start_time", ""),
            "end_time": item.get("end_time", ""),
        }
        all_day, start_time, end_time, timing_error = normalize_entry_timing(timing_payload, allow_equal=True)
        if timing_error:
            return jsonify({"error": f"Ungültige Zeit an Betreuungseintrag {idx + 1}: {timing_error}"}), 400
        restored_end_day, end_error = resolve_entry_end_day(day, all_day, start_time, end_time, item.get("end_day"))
        if end_error:
            return jsonify({"error": f"Ungültiges Bis-Datum an Betreuungseintrag {idx + 1}: {end_error}"}), 400
        normalized_entries.append((
            day, restored_end_day, person, str(item.get("note", "")), all_day, start_time, end_time,
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

    normalized_subscriptions = []
    for idx, item in enumerate(subscriptions):
        if not isinstance(item, dict):
            return jsonify({"error": f"Ungültiges Kalender-Abo an Position {idx + 1}"}), 400
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url:
            continue
        safe, error = _subscription_url_is_safe(url)
        if not safe:
            return jsonify({"error": f"Kalender-Abo {name}: {error}"}), 400
        kind = str(item.get("kind", "holiday")).strip() or "holiday"
        default_color = "#d65a6f" if kind == "holiday" else "#f2a65a" if kind == "vacation" else "#80a4c2"
        now = datetime.now().isoformat(timespec="seconds")
        normalized_subscriptions.append((
            name, url, kind, valid_color(item.get("color"), default_color), 1 if item.get("enabled", True) else 0,
            str(item.get("last_sync_at", "")), str(item.get("last_status", "")),
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
            conn.execute("DELETE FROM calendar_subscriptions")
            conn.executemany(
                "INSERT INTO people(name,color,sort_order,ical_title,calendar_token) VALUES(?,?,?,?,?)",
                normalized_people,
            )
            person_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM people").fetchall()}
            conn.executemany(
                """INSERT INTO entries(day,end_day,person_id,note,all_day,start_time,end_time,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                [(day, end_day, person_ids[person], note, all_day, start_time, end_time, created_at, updated_at)
                 for day, end_day, person, note, all_day, start_time, end_time, created_at, updated_at in normalized_entries],
            )
            conn.executemany(
                """INSERT INTO periods(start_day,end_day,kind,label,color,source,external_uid,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                normalized_periods,
            )
            conn.executemany(
                """INSERT INTO calendar_subscriptions(name,url,kind,color,enabled,last_sync_at,last_status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""", normalized_subscriptions
            )
    except sqlite3.Error as exc:
        return jsonify({"error": f"Import fehlgeschlagen: {exc}"}), 400

    backup_db("after-full-import")
    if normalized_subscriptions:
        threading.Thread(target=sync_all_calendar_subscriptions, name="calendar-subscriptions-restore", daemon=True).start()
    return jsonify({
        "ok": True,
        "people": len(normalized_people),
        "entries": len(normalized_entries),
        "periods": len(normalized_periods),
        "calendar_subscriptions": len(normalized_subscriptions),
    })


def ical_event_title(r):
    """Resolve per-person iCal title first, then fall back to the global template."""
    person = str(r["person"])
    custom = str(r.get("ical_title") or "").strip()
    template = custom or get_setting("ical_title_template", "{person}")
    return template.replace("{person}", person).replace("{app}", APP_TITLE)


def entry_ics_event_lines(r, host):
    day_compact = r["day"].replace("-", "")
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VEVENT",
        f"UID:betreuung-{r['id']}@{host}",
        f"DTSTAMP:{dtstamp}",
    ]
    if r.get("all_day", 1):
        next_day = (date.fromisoformat(r["day"]) + timedelta(days=1)).strftime("%Y%m%d")
        lines.extend([
            f"DTSTART;VALUE=DATE:{day_compact}",
            f"DTEND;VALUE=DATE:{next_day}",
        ])
    else:
        start_compact = str(r.get("start_time") or "").replace(":", "")
        end_compact = str(r.get("end_time") or "").replace(":", "")
        end_day = date.fromisoformat(entry_effective_end_day(r))
        end_day_compact = end_day.strftime("%Y%m%d")
        lines.extend([
            f"DTSTART:{day_compact}T{start_compact}00",
            f"DTEND:{end_day_compact}T{end_compact}00",
        ])
    lines.extend([
        f"SUMMARY:{ics_escape(ical_event_title(r))}",
        f"DESCRIPTION:{ics_escape(r['note'])}",
        "CATEGORIES:Betreuung",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "END:VEVENT",
    ])
    return lines


def build_ics_calendar(rows, host, calendar_name):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Betreuungsplan//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
    ]
    for row in rows:
        lines.extend(entry_ics_event_lines(row, host))
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def rows_for_calendar_range(start_day, end_day, people=None, search=""):
    """Return entries whose actual event interval overlaps the inclusive date range."""
    people = [p for p in (people or []) if p]
    clauses = ["e.day <= ?", "COALESCE(NULLIF(e.end_day,''), e.day) >= ?"]
    params = [end_day, start_day]
    if people:
        placeholders = ",".join("?" for _ in people)
        clauses.append(f"p.name IN ({placeholders})")
        params.extend(people)
    if search:
        clauses.append("(LOWER(e.note) LIKE ? OR LOWER(p.name) LIKE ?)")
        needle = f"%{search.lower()}%"
        params.extend([needle, needle])
    rows = entry_rows(" AND ".join(clauses), tuple(params))
    wanted_start = date.fromisoformat(start_day)
    wanted_end = date.fromisoformat(end_day)
    return [row for row in rows if date.fromisoformat(entry_effective_end_day(row)) >= wanted_start and date.fromisoformat(row["day"]) <= wanted_end]


@app.get("/export.ics")
@login_required
def export_ics_range():
    start_day = valid_iso_day(request.args.get("from", ""))
    end_day = valid_iso_day(request.args.get("to", ""))
    if not start_day or not end_day:
        return Response("Ungültiger Zeitraum", status=400)
    if start_day > end_day:
        return Response("Von-Datum liegt nach Bis-Datum", status=400)
    if (date.fromisoformat(end_day) - date.fromisoformat(start_day)).days > 3660:
        return Response("Zeitraum ist zu groß", status=400)

    people = [p.strip() for p in request.args.getlist("person") if p.strip()]
    search = request.args.get("q", "").strip()
    rows = rows_for_calendar_range(start_day, end_day, people, search)
    host = request.host.split(":")[0]
    # The one-time ICS export uses a dedicated configurable calendar name.
    # This is independent from the individual event titles.
    calendar_name = ICAL_EXPORT_NAME
    body = build_ics_calendar(rows, host, calendar_name)
    filename = f"betreuung-{start_day}-bis-{end_day}.ics"
    disposition = "inline" if request.args.get("open") == "1" else "attachment"
    return Response(
        body,
        mimetype="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/entries/<int:entry_id>/ics")
@login_required
def entry_ics_export(entry_id):
    rows = entry_rows("e.id = ?", (entry_id,))
    if not rows:
        return Response("Not found", status=404)
    row = rows[0]
    host = request.host.split(":")[0]
    body = build_ics_calendar([row], host, f"{APP_TITLE} – {row['person']}")
    filename = f"betreuung-{row['day']}-{entry_id}.ics"
    return Response(
        body,
        mimetype="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store, max-age=0",
        },
    )


@app.get("/calendar.ics")
def calendar_ics():
    token = request.args.get("token", "")
    person_id_raw = request.args.get("person_id", "").strip()
    person_row = None
    if person_id_raw:
        try:
            person_id = int(person_id_raw)
        except ValueError:
            return Response("Not found", status=404)
        with db() as conn:
            person_row = conn.execute("SELECT id,name,calendar_token FROM people WHERE id=?", (person_id,)).fetchone()
        if not person_row or not hmac.compare_digest(token, person_row["calendar_token"]):
            return Response("Not found", status=404)
        rows = entry_rows("p.id = ?", (person_id,))
    else:
        if not ICAL_TOKEN or not hmac.compare_digest(token, ICAL_TOKEN):
            return Response("Not found", status=404)
        rows = entry_rows()

    host = request.host.split(":")[0]
    cal_name = f"{APP_TITLE} – {person_row['name']}" if person_row else APP_TITLE
    body = build_ics_calendar(rows, host, cal_name)
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


# Background calendar subscription synchronization (single gunicorn worker in the provided Dockerfile).
subscription_worker_started = threading.Thread(target=calendar_subscription_worker, name="calendar-subscriptions", daemon=True)
subscription_worker_started.start()
