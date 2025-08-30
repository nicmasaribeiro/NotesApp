# integrations.py
import os, json
from datetime import datetime
from flask import Blueprint, redirect, url_for, render_template, flash
from flask_login import login_required, current_user
from authlib.integrations.flask_client import OAuth
from extensions import db
from organizer import Integration  # your model

integrations_bp = Blueprint("integrations", __name__, template_folder="templates")
oauth = OAuth()  # NOTE: no app bound here yet

def init_oauth(app):
    """Call this from app.py after creating the Flask app."""
    oauth.init_app(app)

    # ---- GitHub OAuth ----
    oauth.register(
        name="github",
        client_id=os.getenv("GITHUB_CLIENT_ID"),
        client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com",
        client_kwargs={"scope": "repo user"},
    )

    # ---- Google OAuth2 (with offline access for refresh tokens) ----
    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        access_token_url="https://oauth2.googleapis.com/token",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        api_base_url="https://www.googleapis.com/",
        client_kwargs={
            "scope": os.getenv(
                "GOOGLE_SCOPES",
                "openid email profile https://www.googleapis.com/auth/calendar.readonly"
            ),
            "prompt": "consent",         # force refresh_token issuance
            "access_type": "offline",
            "include_granted_scopes": "true",
        },
        # Persist refreshed tokens automatically:
        update_token=lambda token, refresh_token=None, access_token=None: _save_google_token(token),
    )

def _save_google_token(token):
    try:
        entry = Integration.query.filter_by(owner_id=current_user.id, provider="google").first()
        if entry:
            entry.token_json = json.dumps(token)
            entry.refreshed_at = datetime.utcnow()
            db.session.commit()
    except Exception:
        pass  # avoid failing the request on token update path

# --------- Views (unchanged from before) ---------
@integrations_bp.route("/settings/connections")
@login_required
def connections():
    gh = Integration.query.filter_by(owner_id=current_user.id, provider="github").first()
    gg = Integration.query.filter_by(owner_id=current_user.id, provider="google").first()
    return render_template("settings/connections.html", gh=gh, gg=gg)

@integrations_bp.route("/auth/github")
@login_required
def auth_github():
    return oauth.github.authorize_redirect(url_for("integrations.auth_github_callback", _external=True))

@integrations_bp.route("/auth/github/callback")
@login_required
def auth_github_callback():
    token = oauth.github.authorize_access_token()
    if not token or "access_token" not in token:
        flash("GitHub authorization failed", "warning")
        return redirect(url_for("integrations.connections"))
    _upsert_token("github", token)
    flash("GitHub connected!", "success")
    return redirect(url_for("integrations.connections"))

@integrations_bp.route("/auth/github/disconnect", methods=["POST"])
@login_required
def auth_github_disconnect():
    _delete_token("github")
    flash("GitHub disconnected.", "success")
    return redirect(url_for("integrations.connections"))

@integrations_bp.route("/auth/google")
@login_required
def auth_google():
    return oauth.google.authorize_redirect(url_for("integrations.auth_google_callback", _external=True))

@integrations_bp.route("/auth/google/callback")
@login_required
def auth_google_callback():
    token = oauth.google.authorize_access_token()
    if not token or "access_token" not in token:
        flash("Google authorization failed", "warning")
        return redirect(url_for("integrations.connections"))
    _upsert_token("google", token)
    flash("Google connected!", "success")
    return redirect(url_for("integrations.connections"))

@integrations_bp.route("/auth/google/disconnect", methods=["POST"])
@login_required
def auth_google_disconnect():
    _delete_token("google")
    flash("Google disconnected.", "success")
    return redirect(url_for("integrations.connections"))

def _upsert_token(provider, token):
    entry = Integration.query.filter_by(owner_id=current_user.id, provider=provider).first()
    if not entry:
        entry = Integration(owner_id=current_user.id, provider=provider)
        db.session.add(entry)
    entry.token_json = json.dumps(token)
    entry.refreshed_at = datetime.utcnow()
    db.session.commit()

def _delete_token(provider):
    entry = Integration.query.filter_by(owner_id=current_user.id, provider=provider).first()
    if entry:
        db.session.delete(entry)
        db.session.commit()
