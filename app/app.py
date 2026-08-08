import csv
import hmac
import io
import os
import secrets
import sqlite3
import threading
from datetime import datetime, date
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

APP_TITLE = os.getenv("APP_TITLE", "Noemi Betreuung")
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "betreuung.sqlite"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_KEEP = max(5, int(os.getenv("BACKUP_KEEP", "50")))
APP_USER = os.getenv("APP_USER", "familie")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
ICAL_TOKEN = os.getenv("ICAL_TOKEN", "")

if not APP_PASSWORD:
    raise RuntimeError("APP_PASSWORD muss gesetzt sein.")

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
    error = None
    if request.method == "POST":
        user = request.form.get("user", "")
        password = request.form.get("password", "")
        ok_user = hmac.compare_digest(user, APP_USER)
        ok_pass = hmac.compare_digest(password, APP_PASSWORD)
        if ok_user and ok_pass:
            session.clear()
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Benutzername oder Passwort ist falsch."
    return render_template("login.html", title=APP_TITLE, error=error)


@app.post("/logout")
def logout():
    session.clear()
    response = redirect(url_for("login"))
    response.headers["Clear-Site-Data"] = '"cache"'
    return response


@app.get("/")
@login_required
def index():
    return render_template("index.html", title=APP_TITLE)


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


def filtered_entry_rows(year="", person="", search=""):
    clauses = []
    params = []
    if year:
        clauses.append("e.day LIKE ?")
        params.append(f"{year}-%")
    if person:
        clauses.append("p.name = ?")
        params.append(person)
    if search:
        clauses.append("(LOWER(e.note) LIKE ? OR LOWER(p.name) LIKE ?)")
        needle = f"%{search.lower()}%"
        params.extend([needle, needle])
    return entry_rows(" AND ".join(clauses), tuple(params)) if clauses else entry_rows()


def export_filename(extension, year="", person=""):
    safe_person = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "-" for c in person).strip("-")
    parts = ["betreuung"]
    if safe_person:
        parts.append(safe_person)
    if year:
        parts.append(year)
    if not safe_person and not year:
        parts.append("alle")
    return "-".join(parts) + extension


@app.get("/export.csv")
@login_required
def export_csv():
    year = request.args.get("year", "").strip()
    person = request.args.get("person", "").strip()
    search = request.args.get("q", "").strip()
    rows = filtered_entry_rows(year, person, search)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Datum", "Betreuung", "Bemerkung"])
    for r in rows:
        w.writerow([r["day"], r["person"], r["note"]])

    body = "\ufeff" + buf.getvalue()
    return Response(
        body,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{export_filename(".csv", year, person)}"'},
    )


@app.get("/export.pdf")
@login_required
def export_pdf():
    year = request.args.get("year", "").strip()
    person = request.args.get("person", "").strip()
    search = request.args.get("q", "").strip()
    rows = filtered_entry_rows(year, person, search)

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Betreuungsliste {person or 'Alle'} {year}".strip(),
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

    label = person or "Alle Betreuungspersonen"
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
    filename = export_filename(".pdf", year, person)
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
        "PRODID:-//Noemi Betreuung//DE",
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
