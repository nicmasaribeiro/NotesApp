# organizer.py
from datetime import datetime, timedelta
import json
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

# If your app exposes these:
# from app import db, requires_oauth  # adjust as needed
from extensions import db

# Optional: PyGithub + Google API client
try:
    from github import Github
except Exception:
    Github = None

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except Exception:
    Credentials = None
    build = None

organizer_bp = Blueprint("organizer", __name__, template_folder="templates")

# ---------- MODELS ----------
class Project(db.Model):
    __tablename__ = "org_projects"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, index=True)  # FK to users.id in your app (optional)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.Integer, default=2)  # 1=High,2=Med,3=Low
    status = db.Column(db.String(32), default="active")  # active|paused|done|archived
    due_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    tasks = db.relationship("Task", backref="project", cascade="all, delete-orphan")

class Task(db.Model):
    __tablename__ = "org_tasks"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("org_projects.id"))
    owner_id = db.Column(db.Integer, index=True)  # optional
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.Integer, default=2)  # 1=High,2=Med,3=Low
    status = db.Column(db.String(32), default="todo")  # todo|doing|done|blocked
    due_date = db.Column(db.DateTime)
    estimate_minutes = db.Column(db.Integer)  # rough estimate
    time_spent_minutes = db.Column(db.Integer, default=0)
    labels = db.Column(db.Text)  # CSV or JSON
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Dashboard(db.Model):
    __tablename__ = "org_dashboards"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, index=True)
    name = db.Column(db.String(140), nullable=False)
    layout_json = db.Column(db.Text, default="{}")  # widget layout/settings
    filters_json = db.Column(db.Text, default="{}")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Integration(db.Model):
    __tablename__ = "org_integrations"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, index=True)
    provider = db.Column(db.String(32))  # 'github' | 'google'
    token_json = db.Column(db.Text)  # store OAuth tokens (encrypt at rest in prod)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    refreshed_at = db.Column(db.DateTime)

# ---------- HELPERS ----------
def _prio_score(priority: int) -> int:
    # Lower is more important; convert to a positive score (bigger = higher priority)
    # 1->100, 2->60, 3->30
    return {1: 100, 2: 60, 3: 30}.get(priority or 2, 60)

def task_rank(task: Task) -> float:
    """Simple prioritization: priority weight + due-date proximity."""
    base = _prio_score(task.priority)
    if task.due_date:
        days_left = (task.due_date - datetime.utcnow()).days
        urgency = max(0, 30 - days_left)  # closer due dates get more bump
    else:
        urgency = 0
    return base + urgency

def get_github_client(owner_id):
    if Github is None:
        return None
    integ = Integration.query.filter_by(owner_id=owner_id, provider="github").first()
    token = None
    if integ:
        try:
            token = json.loads(integ.token_json).get("access_token")
        except Exception:
            token = None
    if not token:
        token = os.getenv("GITHUB_TOKEN")  # fallback to env for dev
    return Github(token) if token else None

def get_google_calendar(owner_id):
    if Credentials is None or build is None:
        return None
    integ = Integration.query.filter_by(owner_id=owner_id, provider="google").first()
    if not integ:
        return None
    try:
        creds = Credentials.from_authorized_user_info(json.loads(integ.token_json))
        service = build("calendar", "v3", credentials=creds)
        return service
    except Exception:
        return None

# ---------- ROUTES ----------
@organizer_bp.route("/organizer")
@login_required
def index():
    # upcoming tasks (next 14 days), ranked
    now = datetime.utcnow()
    soon = now + timedelta(days=14)
    tasks = (
        Task.query.filter(Task.owner_id == current_user.id)
        .filter((Task.due_date == None) | (Task.due_date <= soon))  # noqa: E711
        .options(joinedload(Task.project))
        .all()
    )
    tasks_sorted = sorted(tasks, key=task_rank, reverse=True)[:50]

    # events (Google)
    events = []
    service = get_google_calendar(current_user.id)
    if service:
        try:
            resp = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat() + "Z",
                    timeMax=soon.isoformat() + "Z",
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for e in resp.get("items", []):
                start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
                events.append({"summary": e.get("summary"), "start": start})
        except Exception:
            events = []

    # GitHub heads-up
    gh_issues = []
    gh = get_github_client(current_user.id)
    if gh:
        try:
            for i in gh.get_user().get_issues(state="open")[:10]:
                gh_issues.append(
                    {"title": i.title, "repo": i.repository.full_name, "html_url": i.html_url}
                )
        except Exception:
            gh_issues = []

    projects = Project.query.filter_by(owner_id=current_user.id, status="active").all()
    return render_template(
        "organizer/index.html",
        tasks=tasks_sorted,
        events=events,
        gh_issues=gh_issues,
        projects=projects,
    )

@organizer_bp.route("/organizer/projects", methods=["GET", "POST"])
@login_required
def projects():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Project name is required", "warning")
            return redirect(url_for("organizer.projects"))
        p = Project(
            owner_id=current_user.id,
            name=name,
            description=request.form.get("description"),
            priority=int(request.form.get("priority") or 2),
            due_date=_safe_date(request.form.get("due_date")),
        )
        db.session.add(p)
        db.session.commit()
        flash("Project created", "success")
        return redirect(url_for("organizer.projects"))
    projects = (
        Project.query.filter_by(owner_id=current_user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    return render_template("organizer/projects.html", projects=projects)

def _safe_date(s):
    try:
        if s:
            return datetime.fromisoformat(s)
    except Exception:
        return None
    return None

@organizer_bp.route("/organizer/projects/<int:pid>")
@login_required
def project_detail(pid):
    p = Project.query.filter_by(id=pid, owner_id=current_user.id).first_or_404()
    tasks = (
        Task.query.filter_by(project_id=p.id)
        .order_by(Task.status.asc(), Task.priority.asc(), Task.due_date.asc().nullslast())
        .all()
    )
    return render_template("organizer/project_detail.html", p=p, tasks=tasks)

@organizer_bp.route("/organizer/tasks", methods=["POST"])
@login_required
def create_task():
    title = request.form.get("title", "").strip()
    if not title:
        flash("Task title is required", "warning")
        return redirect(request.referrer or url_for("organizer.index"))
    t = Task(
        owner_id=current_user.id,
        project_id=int(request.form.get("project_id")) if request.form.get("project_id") else None,
        title=title,
        description=request.form.get("description"),
        priority=int(request.form.get("priority") or 2),
        status=request.form.get("status") or "todo",
        due_date=_safe_date(request.form.get("due_date")),
        estimate_minutes=int(request.form.get("estimate_minutes") or 0),
        labels=request.form.get("labels"),
    )
    db.session.add(t)
    db.session.commit()
    flash("Task created", "success")
    return redirect(request.referrer or url_for("organizer.index"))

@organizer_bp.route("/organizer/tasks/<int:tid>/update", methods=["POST"])
@login_required
def update_task(tid):
    t = Task.query.filter_by(id=tid, owner_id=current_user.id).first_or_404()
    for k in ["title", "description", "status", "labels"]:
        v = request.form.get(k)
        if v is not None:
            setattr(t, k, v)
    for k in ["priority", "estimate_minutes", "time_spent_minutes"]:
        if request.form.get(k):
            setattr(t, k, int(request.form.get(k)))
    if "due_date" in request.form:
        t.due_date = _safe_date(request.form.get("due_date"))
    db.session.commit()
    flash("Task updated", "success")
    return redirect(request.referrer or url_for("organizer.index"))

@organizer_bp.route("/organizer/tasks/<int:tid>/toggle", methods=["POST"])
@login_required
def toggle_task(tid):
    t = Task.query.filter_by(id=tid, owner_id=current_user.id).first_or_404()
    t.status = "done" if t.status != "done" else "todo"
    db.session.commit()
    return jsonify({"ok": True, "status": t.status})

# ---------- INTEGRATIONS ----------
@organizer_bp.route("/organizer/github/sync", methods=["POST"])
@login_required
def github_sync():
    """Import open GitHub issues assigned to user as tasks (lightweight)."""
    gh = get_github_client(current_user.id)
    if not gh:
        return jsonify({"ok": False, "error": "GitHub not configured"}), 400
    created = 0
    try:
        for i in gh.get_user().get_issues(state="open"):
            title = f"[GH] {i.title}"
            exists = Task.query.filter_by(owner_id=current_user.id, title=title).first()
            if exists:
                continue
            t = Task(owner_id=current_user.id, title=title, description=i.html_url, priority=2)
            db.session.add(t)
            created += 1
        db.session.commit()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "created": created})

@organizer_bp.route("/organizer/google/events.json")
@login_required
def google_events_json():
    service = get_google_calendar(current_user.id)
    if not service:
        return jsonify({"events": []})
    now = datetime.utcnow()
    soon = now + timedelta(days=14)
    try:
        resp = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat() + "Z",
                timeMax=soon.isoformat() + "Z",
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        items = [
            {
                "summary": e.get("summary"),
                "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
            }
            for e in resp.get("items", [])
        ]
        return jsonify({"events": items})
    except Exception:
        return jsonify({"events": []})

# ---------- DASHBOARDS ----------
@organizer_bp.route("/organizer/dashboards", methods=["GET", "POST"])
@login_required
def dashboards():
    if request.method == "POST":
        name = request.form.get("name", "My Dashboard").strip()
        d = Dashboard(owner_id=current_user.id, name=name, layout_json=json.dumps({}))
        db.session.add(d)
        db.session.commit()
        return redirect(url_for("organizer.dashboard_detail", did=d.id))
    boards = Dashboard.query.filter_by(owner_id=current_user.id).all()
    return render_template("organizer/dashboards.html", boards=boards)

@organizer_bp.route("/organizer/dashboards/<int:did>")
@login_required
def dashboard_detail(did):
    d = Dashboard.query.filter_by(id=did, owner_id=current_user.id).first_or_404()
    layout = json.loads(d.layout_json or "{}")
    return render_template("organizer/dashboard_detail.html", d=d, layout=layout)

@organizer_bp.route("/organizer/dashboards/<int:did>/save", methods=["POST"])
@login_required
def dashboard_save(did):
    d = Dashboard.query.filter_by(id=did, owner_id=current_user.id).first_or_404()
    d.layout_json = request.data.decode("utf-8") or "{}"
    db.session.commit()
    return jsonify({"ok": True})
