from datetime import datetime
from pathlib import Path
from typing import List

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, jsonify, abort
)
from sqlalchemy import create_engine, Integer, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
import markdown as md
import bleach
import io
from sqlalchemy import Boolean
from secrets import token_urlsafe
from flask_socketio import SocketIO, emit, join_room, leave_room
from threading import Lock
from sqlalchemy import ForeignKey, Boolean, select
from sqlalchemy.orm import relationship
from flask import render_template, request, redirect, url_for, flash
from typing import Optional
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user, login_required
)
from typing import Optional
from sqlalchemy import Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from secrets import token_urlsafe
from sqlalchemy import select
from flask_login import login_required, current_user
from flask import render_template, request, redirect, url_for, flash, abort
from biblio import biblio, BibEntry
from extensions import db
import os
from biblio_bp import biblio_bp
from organizer import organizer_bp
from flask import Flask, redirect, url_for, session
from authlib.integrations.flask_client import OAuth
from integrations import integrations_bp, init_oauth, oauth


APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "docs.sqlite3"

# -----------------------------
# Flask
# -----------------------------
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///nmbc.sqlite3"
app.config["SECRET_KEY"] = os.urandom(10).hex()
app.config["SERVER_NAME"] = "127.0.0.1:90"  # or "localhost:5000"

db.init_app(app)
init_oauth(app)

# # in app.py after creating app
# def _mask(v):
#     if not v: return "MISSING"
#     return f"{v[:6]}...{v[-4:]}"
# print("[OAUTH] GOOGLE_CLIENT_ID:", _mask(os.getenv("GOOGLE_CLIENT_ID")))
# print("[OAUTH] GOOGLE_CLIENT_SECRET:", _mask(os.getenv("GOOGLE_CLIENT_SECRET")))
# print("[OAUTH] SERVER_NAME:", app.config.get("SERVER_NAME"))



# google = oauth.register(
#     name='google',
#     client_id="YOUR_GOOGLE_CLIENT_ID",
#     client_secret="YOUR_GOOGLE_CLIENT_SECRET",
#     access_token_url="https://accounts.google.com/o/oauth2/token",
#     access_token_params=None,
#     authorize_url="https://accounts.google.com/o/oauth2/auth",
#     authorize_params={"prompt": "select_account"},
#     api_base_url="https://www.googleapis.com/oauth2/v1/",
#     userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
#     client_kwargs={"scope": "openid email profile"},
# )

with app.app_context():
    db.create_all()

app.register_blueprint(biblio,url_prefix="/bib")
app.register_blueprint(organizer_bp)
app.register_blueprint(integrations_bp)

# app.register_blueprint(biblio_bp)


# --- Socket.IO setup ---
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading"   # <— force threading; avoids eventlet/gevent import
) # eventlet/gevent best; threads fallback OK
save_lock = Lock()

login_manager = LoginManager(app)
login_manager.login_view = "login"

# -----------------------------
# SQLAlchemy
# -----------------------------

# --- imports/snips you likely already have ---
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    create_engine, Integer, String, Text, DateTime, ForeignKey, Boolean, select
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# 1) Single Base for EVERY model
class Base(DeclarativeBase):
    pass

# 2) Declare User FIRST (so 'users' is in metadata)
class User(Base, UserMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")

    # ✅ Add these two:
    def set_password(self, pw: str) -> None:
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    with SessionLocal() as db:
        return db.get(User, int(user_id))

# 3) Other models AFTER User
class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), default="Untitled")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    owner = relationship("User", back_populates="documents")


class SharedLink(Base):
    __tablename__ = "shared_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # optional, helpful for listing a user's shares
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# 4) Engine + create_all AFTER all models are defined
engine = create_engine(f"sqlite:///docs.sqlite3", future=True)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# -----------------------------
# Markdown -> HTML (server-side)
# We keep math as-is ($...$, $$...$$) and let KaTeX render it on the client.
# -----------------------------
MD_EXTS = [
    "fenced_code",
    "tables",
    "toc",
    "sane_lists",
    "admonition",
]

ALLOWED_TAGS: List[str] = bleach.sanitizer.ALLOWED_TAGS.union(
    {"p","pre","code","span","div","h1","h2","h3","h4","h5","h6",
     "table","thead","tbody","tr","th","td","hr","br","blockquote","ul","ol","li"}
)
ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "target", "rel"],
    "span": ["class"],
    "div": ["class"],
    "code": ["class"],
    "pre": ["class"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

def ensure_schema():
    with engine.begin() as conn:
        # create missing tables
        Base.metadata.create_all(bind=conn)
        # add owner_id to documents if missing (SQLite)
        cols = conn.exec_driver_sql("PRAGMA table_info(documents)").fetchall()
        names = {c[1] for c in cols}
        if "owner_id" not in names:
            conn.exec_driver_sql("ALTER TABLE documents ADD COLUMN owner_id INTEGER")
ensure_schema()

def render_markdown(text: str) -> str:
    # Convert markdown to HTML, leaving $...$ for KaTeX to handle in the browser.
    html = md.markdown(text, extensions=MD_EXTS, output_format="html5")
    safe = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS,
                        protocols=ALLOWED_PROTOCOLS, strip=True)
    return safe

from typing import Optional
def get_share(token: str, db: Session) -> Optional[SharedLink]:
    return db.query(SharedLink).filter(SharedLink.token == token).first()

def get_latest_version(doc_id: int, db: Session) -> int:
    rev = (
        db.query(DocumentRevision)
        .filter(DocumentRevision.document_id == doc_id)
        .order_by(DocumentRevision.version.desc())
        .first()
    )
    return rev.version if rev else 1

def create_revision(doc: Document, new_content: str, db: Session) -> int:
    # bump version, write revision, persist
    current_version = get_latest_version(doc.id, db)
    new_version = current_version + 1
    r = DocumentRevision(document_id=doc.id, version=new_version, content=new_content)
    doc.content = new_content
    db.add(r)
    db.add(doc)
    db.commit()
    return new_version

# -----------------------------
# Routes
# -----------------------------
@app.route("/login/google")
def login_google():
    redirect_uri = url_for("auth_google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/auth/callback/google")
def auth_google_callback():
    token = google.authorize_access_token()
    userinfo = google.parse_id_token(token)

    # Example userinfo contains: {"sub": "...", "email": "...", "name": "..."}
    session["user"] = userinfo

    # TODO: integrate with your User model & Flask-Login
    return redirect(url_for("dashboard"))  # or wherever you want

def _doc_for_user_or_404(db, doc_id: int):
    d = db.get(Document, doc_id)
    if not d or d.owner_id != current_user.id:
        abort(404)
    return d

@app.route("/doc/<int:doc_id>/shares")
@login_required
def list_shares(doc_id: int):
    with SessionLocal() as db:
        d = _doc_for_user_or_404(db, doc_id)
        shares = db.execute(
            select(SharedLink).where(SharedLink.document_id == d.id)
        ).scalars().all()
        return render_template("shares.html", doc=d, shares=shares)

@app.route("/doc/<int:doc_id>/shares/create", methods=["POST"])
@login_required
def create_share_for_doc(doc_id: int):
    can_edit = (request.form.get("can_edit") == "on")
    with SessionLocal() as db:
        d = _doc_for_user_or_404(db, doc_id)
        tok = token_urlsafe(16)
        s = SharedLink(document_id=d.id, token=tok, can_edit=can_edit, owner_id=current_user.id)
        db.add(s)
        # ensure a starting revision exists if you use revisions
        if "DocumentRevision" in globals():
            if get_latest_version(d.id, db) == 1:
                db.add(DocumentRevision(document_id=d.id, version=1, content=d.content))
        db.commit()
        link = url_for("open_shared", token=tok, _external=True)
        flash(("Edit" if can_edit else "View") + f" link created: {link}", "ok")
        return redirect(url_for("list_shares", doc_id=d.id))

@app.route("/share/<string:token>/toggle", methods=["POST"])
@login_required
def toggle_share(token: str):
    with SessionLocal() as db:
        s = db.execute(select(SharedLink).where(SharedLink.token == token)).scalar()
        if not s:
            abort(404)
        d = _doc_for_user_or_404(db, s.document_id)
        s.can_edit = not s.can_edit
        db.add(s); db.commit()
        flash(f"Permissions updated: {'Editable' if s.can_edit else 'View only'}", "ok")
        return redirect(url_for("list_shares", doc_id=d.id))

@app.route("/share/<string:token>/revoke", methods=["POST"])
@login_required
def revoke_share(token: str):
    with SessionLocal() as db:
        s = db.execute(select(SharedLink).where(SharedLink.token == token)).scalar()
        if not s:
            abort(404)
        d = _doc_for_user_or_404(db, s.document_id)
        db.delete(s); db.commit()
        flash("Link revoked.", "ok")
        return redirect(url_for("list_shares", doc_id=d.id))

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not email or not username or not password:
            flash("All fields are required.", "err")
            return render_template("auth_register.html")

        with SessionLocal() as db:
            # Uniqueness checks
            exists = db.execute(select(User).where((User.email == email) | (User.username == username))).scalar()
            if exists:
                flash("Email or username already taken.", "err")
                return render_template("auth_register.html")
            u = User(email=email, username=username)
            u.set_password(password)
            db.add(u)
            db.commit()
            login_user(u)
            flash("Welcome!", "ok")
            return redirect(url_for("index"))
    return render_template("auth_register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email_or_username = (request.form.get("id") or "").strip()
        password = request.form.get("password") or ""

        with SessionLocal() as db:
            q = select(User).where((User.email == email_or_username.lower()) | (User.username == email_or_username))
            u = db.execute(q).scalar()
            if not u or not u.check_password(password):
                flash("Invalid credentials.", "err")
                return render_template("auth_login.html")
            login_user(u)
            flash("Logged in.", "ok")
            return redirect(url_for("index"))
    return render_template("auth_login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "ok")
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    with SessionLocal() as db:
        docs = (
            db.query(Document)
            .filter(Document.owner_id == current_user.id)
            .order_by(Document.updated_at.desc())
            .all()
        )
    return render_template("index.html", docs=docs)

@app.route("/new", methods=["GET", "POST"])
@login_required
def new_doc():
    if request.method == "POST":
        title = request.form.get("title") or "Untitled"
        content = request.form.get("content") or ""
        with SessionLocal() as db:
            d = Document(title=title, content=content, owner_id=current_user.id)
            db.add(d)
            db.commit()
            flash("Document created.", "ok")
            return redirect(url_for("edit_doc", doc_id=d.id))
    return render_template("editor.html", doc=None)

@app.route("/edit/<int:doc_id>", methods=["GET", "POST"])
@login_required
def edit_doc(doc_id: int):
    with SessionLocal() as db:
        d = db.get(Document, doc_id)
        if not d or d.owner_id != current_user.id:
            abort(404)
        if request.method == "POST":
            d.title = request.form.get("title") or d.title
            d.content = request.form.get("content") or ""
            db.add(d)
            db.commit()
            flash("Document saved.", "ok")
            return redirect(url_for("edit_doc", doc_id=doc_id))
    return render_template("editor.html", doc=d)

@app.route("/api/preview", methods=["POST"])
def api_preview():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    html = render_markdown(text)
    return jsonify({"html": html})

@app.route("/download/<int:doc_id>")
def download_md(doc_id: int):
    with SessionLocal() as db:
        d = db.get(Document, doc_id)
        if not d:
            abort(404)
        buf = io.BytesIO(d.content.encode("utf-8"))
        filename = f"{d.title or 'document'}.md"
        return send_file(buf, as_attachment=True, download_name=filename, mimetype="text/markdown")

@app.route("/export/html/<int:doc_id>")
def export_html(doc_id: int):
    with SessionLocal() as db:
        d = db.get(Document, doc_id)
        if not d:
            abort(404)
        body_html = render_markdown(d.content)
        full_html = render_template("export.html", title=d.title, body_html=body_html)
        buf = io.BytesIO(full_html.encode("utf-8"))
        filename = f"{d.title or 'document'}.html"
        return send_file(buf, as_attachment=True, download_name=filename, mimetype="text/html")

# Helpful: quick seed route (optional)
@app.route("/seed")
def seed():
    example = r"""
# Markdown + LaTeX Demo

Inline math like $E=mc^2$ and display blocks:

$$
\nabla \cdot \vec{E} = \frac{\rho}{\varepsilon_0}
$$

Tables:

| Col A | Col B |
|------:|:------|
|  1.23 | text  |
|  4.56 | more  |

Code:

```python
def hello():
    return "world"```
"""

from flask import make_response

@app.route("/share/<int:doc_id>", methods=["POST"])
def create_share(doc_id: int):
    """Create a share link. POST form fields: can_edit=on/off."""
    can_edit = (request.form.get("can_edit") == "on")
    with SessionLocal() as db:
        d = db.get(Document, doc_id)
        if not d:
            abort(404)
        token = token_urlsafe(16)
        s = SharedLink(document_id=doc_id, token=token, can_edit=can_edit,
               owner_id=(current_user.id if current_user.is_authenticated else None))
        db.add(s)
        # ensure a starting revision exists
        if get_latest_version(doc_id, db) == 1:
            db.add(DocumentRevision(document_id=doc_id, version=1, content=d.content))
        db.commit()
        link = url_for("open_shared", token=token, _external=True)
        flash(("Edit" if can_edit else "View") + " link created.", "ok")
        # Return the link as plain text (or redirect back to editor with flash)
        return make_response(link, 201)

@app.route("/s/<string:token>")
def open_shared(token: str):
    """Open communal editor/viewer using a token room."""
    with SessionLocal() as db:
        s = get_share(token, db)
        if not s:
            abort(404)
        doc = db.get(Document, s.document_id)
        if not doc:
            abort(404)
        version = get_latest_version(doc.id, db)
        # Render collaborative editor (view-only if can_edit=False)
        return render_template("collab.html",
                               token=token,
                               can_edit=s.can_edit,
                               doc_id=doc.id,
                               title=doc.title,
                               initial_content=doc.content,
                               version=version)

@app.route("/api/share/<string:token>", methods=["GET"])
def api_share_state(token: str):
    """Return the latest content + version for resync."""
    with SessionLocal() as db:
        s = get_share(token, db)
        if not s:
            abort(404)
        doc = db.get(Document, s.document_id)
        if not doc:
            abort(404)
        version = get_latest_version(doc.id, db)
        return jsonify({
            "title": doc.title,
            "content": doc.content,
            "version": version,
            "can_edit": s.can_edit,
            "doc_id": doc.id
        })
# In-memory presence map: {room_token: {sid: username}}
presence: dict[str, dict[str, str]] = {}

@app.route("/render", methods=["GET", "POST"])
def render_index():
    if request.method == "POST":
        uploaded_file = request.files.get("file")
        if uploaded_file:
            # Example: save uploaded file temporarily
            filepath = f"uploads/{uploaded_file.filename}"
            uploaded_file.save(filepath)
            flash("File uploaded successfully!", "success")
            return redirect(url_for("render_index"))
    return render_template("index_render.html")

###################
## Socket.IO Logic
###################

@socketio.on("join")
def ws_join(data):
    token = (data or {}).get("token")
    username = (data or {}).get("username") or (current_user.username if current_user.is_authenticated else "guest")
    if not token:
        return
    join_room(token)
    presence.setdefault(token, {})[request.sid] = username
    emit("presence", {"users": list(presence[token].values())}, to=token)


@socketio.on("leave")
def ws_leave(data):
    token = (data or {}).get("token")
    if token:
        leave_room(token)
    if token in presence and request.sid in presence[token]:
        presence[token].pop(request.sid, None)
        emit("presence", {"users": list(presence[token].values())}, to=token)

@socketio.on("disconnect")
def ws_disconnect():
    # remove from any room presence
    for token, users in list(presence.items()):
        if request.sid in users:
            users.pop(request.sid, None)
            emit("presence", {"users": list(users.values())}, to=token)

@socketio.on("cursor")
def ws_cursor(data):
    """
    data: { token, index, username }
    Broadcast cursor to others in room.
    """
    token = (data or {}).get("token")
    if not token:
        return
    emit("cursor", {
        "index": int((data or {}).get("index", 0)),
        "user": (data or {}).get("username") or "guest",
    }, to=token, include_self=False)

@socketio.on("edit")
def ws_edit(data):
    """
    data: { token, content, base_version, username }
    Server checks base_version == current; if OK -> save, bump version, broadcast.
    If conflict -> emit 'resync' to sender with latest.
    """
    token = (data or {}).get("token")
    new_content = (data or {}).get("content", "")
    base_version = int((data or {}).get("base_version") or 0)

    if not token:
        return

    with SessionLocal() as db, save_lock:
        s = get_share(token, db)
        if not s:
            emit("error", {"message": "Invalid share token."})
            return

        if not s.can_edit:
            emit("error", {"message": "View-only share."})
            return

        doc = db.get(Document, s.document_id)
        if not doc:
            emit("error", {"message": "Document not found."})
            return

        current_version = get_latest_version(doc.id, db)
        if base_version != current_version:
            # client is stale -> send latest
            emit("resync", {
                "content": doc.content,
                "version": current_version
            })
            return

        # Accept edit -> create new revision
        new_version = create_revision(doc, new_content, db)

        # Broadcast to room (including the editor to unify state)
        emit("content", {
            "content": new_content,
            "version": new_version,
            "editor": (data or {}).get("username") or "guest",
        }, to=token)


if __name__ == "__main__":
    # Prefer eventlet for WebSocket support
    try:
        import eventlet
        eventlet.monkey_patch()
        socketio.run(app, host="0.0.0.0", port=90,allow_unsafe_werkzeug=True)#, debug=True)
    except Exception:
        # Fallback (long-polling may be used)
        socketio.run(app, host="0.0.0.0", port=90,allow_unsafe_werkzeug=True)#, debug=True)
