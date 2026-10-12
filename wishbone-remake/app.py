"""Wishbone: a this-or-that voting app. One file of Python, one file of HTML.

Run:  python app.py      then open http://localhost:5001
"""
import os
import re
import secrets
import sqlite3
import uuid
from functools import wraps

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "wishbone.db")
UPLOAD_DIR = os.path.join(BASE, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def load_secret_key():
    """Use SECRET_KEY if set; otherwise make one once and remember it in secret.key."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(BASE, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=load_secret_key(),
    MAX_CONTENT_LENGTH=24 * 1024 * 1024,   # two photos per poll
    SESSION_COOKIE_SAMESITE="Lax",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS polls(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  question TEXT NOT NULL,
  a_label TEXT NOT NULL, a_image TEXT NOT NULL,
  b_label TEXT NOT NULL, b_image TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS votes(
  poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  side TEXT NOT NULL CHECK(side IN ('a','b')),
  PRIMARY KEY(poll_id, user_id)          -- one vote per person per poll
);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


with sqlite3.connect(DB_PATH) as _c:
    _c.executescript(SCHEMA)


def err(msg, code=400):
    return jsonify(error=msg), code


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        uid = session.get("uid")
        if not uid or not db().execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            session.clear()
            return err("Please log in.", 401)
        g.uid = uid
        return fn(*a, **kw)
    return wrapper


@app.errorhandler(413)
def too_big(_):
    return err("Those pictures are too big (24 MB total max).", 413)


# ---------- pictures ----------
def image_type(raw):
    """Identify the real file type from its first bytes (never trust the filename)."""
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw[:4] == b"GIF8":
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def save_picture(file_storage):
    raw = file_storage.read()
    kind = image_type(raw)
    if not kind:
        raise ValueError("Please use JPG, PNG, WebP or GIF pictures.")
    name = f"{uuid.uuid4().hex}.{kind}"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
        f.write(raw)
    return "/uploads/" + name


def delete_picture(url):
    try:
        os.remove(os.path.join(UPLOAD_DIR, os.path.basename(url)))
    except OSError:
        pass


# ---------- pages & files ----------
@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.get("/uploads/<path:name>")
def uploads(name):
    resp = send_from_directory(UPLOAD_DIR, name)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ---------- accounts ----------
def user_json(uid):
    row = db().execute(
        "SELECT u.id, u.username,"
        " (SELECT COUNT(*) FROM votes WHERE user_id=u.id) AS votes_cast,"
        " (SELECT COUNT(*) FROM polls WHERE user_id=u.id) AS polls_made"
        " FROM users u WHERE u.id=?", (uid,)).fetchone()
    return dict(row) if row else None


@app.get("/api/me")
def me():
    uid = session.get("uid")
    return jsonify(user=user_json(uid) if uid else None)


@app.post("/api/register")
def register():
    d = request.get_json(silent=True) or {}
    username, pw = (d.get("username") or "").strip(), d.get("password") or ""
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
        return err("Username must be 3-20 letters, numbers or underscores.")
    if len(pw) < 8:
        return err("Password must be at least 8 characters.")
    try:
        cur = db().execute("INSERT INTO users(username, pw_hash) VALUES(?,?)",
                           (username, generate_password_hash(pw)))
        db().commit()
    except sqlite3.IntegrityError:
        return err("That username is taken.", 409)
    session.clear()
    session["uid"] = cur.lastrowid
    return jsonify(user=user_json(cur.lastrowid)), 201


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    row = db().execute("SELECT id, pw_hash FROM users WHERE username=?",
                       ((d.get("username") or "").strip(),)).fetchone()
    if not row or not check_password_hash(row["pw_hash"], d.get("password") or ""):
        return err("Wrong username or password.", 401)
    session.clear()
    session["uid"] = row["id"]
    return jsonify(user=user_json(row["id"]))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


# ---------- polls ----------
POLL_SQL = """
SELECT p.id, p.question, p.user_id, u.username AS author,
  p.a_label, p.a_image, p.b_label, p.b_image,
  (SELECT COUNT(*) FROM votes v WHERE v.poll_id=p.id AND v.side='a') AS a_votes,
  (SELECT COUNT(*) FROM votes v WHERE v.poll_id=p.id AND v.side='b') AS b_votes,
  (SELECT side FROM votes v WHERE v.poll_id=p.id AND v.user_id=?) AS my_vote
FROM polls p JOIN users u ON u.id = p.user_id
"""


def fetch_polls(where="", params=()):
    rows = db().execute(f"{POLL_SQL} {where} ORDER BY p.id DESC", (g.uid, *params)).fetchall()
    return [{
        "id": r["id"], "question": r["question"], "author": r["author"],
        "mine": r["user_id"] == g.uid, "my_vote": r["my_vote"],
        "a": {"label": r["a_label"], "image": r["a_image"], "votes": r["a_votes"]},
        "b": {"label": r["b_label"], "image": r["b_image"], "votes": r["b_votes"]},
    } for r in rows]


@app.get("/api/polls")
@login_required
def list_polls():
    if request.args.get("scope") == "mine":
        return jsonify(polls=fetch_polls("WHERE p.user_id=?", (g.uid,)))
    return jsonify(polls=fetch_polls("WHERE p.user_id<>?", (g.uid,)))   # the feed


@app.post("/api/polls")
@login_required
def create_poll():
    f = request.form
    question, a_label, b_label = (f.get(k, "").strip() for k in ("question", "a_label", "b_label"))
    if not 1 <= len(question) <= 80:
        return err("Add a question (80 characters max).")
    if not (1 <= len(a_label) <= 24 and 1 <= len(b_label) <= 24):
        return err("Give both options a label (24 characters max).")
    a_file, b_file = request.files.get("a_image"), request.files.get("b_image")
    if not a_file or not b_file or not a_file.filename or not b_file.filename:
        return err("Add a picture for both options.")
    saved = []
    try:
        for fs in (a_file, b_file):
            saved.append(save_picture(fs))
    except ValueError as exc:
        for url in saved:
            delete_picture(url)
        return err(str(exc))
    cur = db().execute(
        "INSERT INTO polls(user_id, question, a_label, a_image, b_label, b_image) VALUES(?,?,?,?,?,?)",
        (g.uid, question, a_label, saved[0], b_label, saved[1]))
    db().commit()
    return jsonify(poll=fetch_polls("WHERE p.id=?", (cur.lastrowid,))[0]), 201


@app.post("/api/polls/<int:pid>/vote")
@login_required
def vote(pid):
    side = (request.get_json(silent=True) or {}).get("side")
    if side not in ("a", "b"):
        return err("side must be 'a' or 'b'.")
    row = db().execute("SELECT user_id FROM polls WHERE id=?", (pid,)).fetchone()
    if not row:
        return err("Poll not found.", 404)
    if row["user_id"] == g.uid:
        return err("You can't vote on your own poll.", 403)
    try:
        db().execute("INSERT INTO votes(poll_id, user_id, side) VALUES(?,?,?)", (pid, g.uid, side))
        db().commit()
    except sqlite3.IntegrityError:
        return err("You already voted on this one.", 409)
    return jsonify(poll=fetch_polls("WHERE p.id=?", (pid,))[0])


@app.delete("/api/polls/<int:pid>")
@login_required
def delete_poll(pid):
    row = db().execute("SELECT a_image, b_image FROM polls WHERE id=? AND user_id=?",
                       (pid, g.uid)).fetchone()
    if not row:
        return err("Poll not found.", 404)
    db().execute("DELETE FROM polls WHERE id=?", (pid,))
    db().commit()
    delete_picture(row["a_image"])
    delete_picture(row["b_image"])
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
