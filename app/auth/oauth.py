from datetime import datetime

from authlib.integrations.flask_client import OAuth
from flask import current_app, flash, redirect, url_for
from flask_login import login_user
from sqlalchemy import or_

from app.auth import auth_bp
from app.extensions import db
from app.models import AuditLog, User


oauth = OAuth()


def init_oauth(app):
    oauth.init_app(app)
    if app.config.get("GOOGLE_CLIENT_ID") and app.config.get("GOOGLE_CLIENT_SECRET"):
        oauth.register(
            name="google",
            client_id=app.config.get("GOOGLE_CLIENT_ID"),
            client_secret=app.config.get("GOOGLE_CLIENT_SECRET"),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    return oauth


@auth_bp.route("/google")
def google_login():
    if not current_app.config.get("GOOGLE_CLIENT_ID") or not current_app.config.get("GOOGLE_CLIENT_SECRET"):
        flash("Google login is not configured.", "warning")
        return redirect(url_for("auth.login"))

    if not hasattr(oauth, "google"):
        flash("Google login is not configured.", "warning")
        return redirect(url_for("auth.login"))

    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
def google_callback():
    if not current_app.config.get("GOOGLE_CLIENT_ID") or not current_app.config.get("GOOGLE_CLIENT_SECRET"):
        flash("Google login is not configured.", "warning")
        return redirect(url_for("auth.login"))

    if not hasattr(oauth, "google"):
        flash("Google login is not configured.", "warning")
        return redirect(url_for("auth.login"))

    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = oauth.google.get("userinfo").json()

    email = userinfo.get("email")
    google_sub = userinfo.get("sub")

    if not email or not google_sub:
        flash("Google login failed. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.filter(or_(User.google_oauth_id == google_sub, User.email == email)).first()
    if user:
        if not user.google_oauth_id:
            user.google_oauth_id = google_sub
        user.last_login_at = datetime.utcnow()
        db.session.commit()

        login_user(user)
        AuditLog.log(action="google_oauth_login", user_id=user.id)
        if not user.is_onboarded:
            return redirect("/onboarding")
        return redirect("/dashboard")

    new_user = User(email=email, google_oauth_id=google_sub, display_name=userinfo.get("name"))
    db.session.add(new_user)
    db.session.commit()

    login_user(new_user)
    AuditLog.log(action="google_oauth_register", user_id=new_user.id)
    return redirect("/onboarding")
