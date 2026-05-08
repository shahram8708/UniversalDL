from datetime import datetime

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_mail import Message
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.auth import auth_bp
from app.auth.forms import LoginForm, RegisterForm, ResetPasswordForm, ResetPasswordRequestForm
from app.extensions import db, mail
from app.models import AuditLog, User
from app.services import notify


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(form.password.data):
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", form=form)
        if user.is_suspended:
            flash("Your account has been suspended.", "danger")
            return render_template("auth/login.html", form=form)

        login_user(user, remember=form.remember_me.data)
        user.last_login_at = datetime.utcnow()
        db.session.commit()

        AuditLog.log(
            action="login",
            user_id=user.id,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

        if not user.is_onboarded:
            return redirect("/onboarding")
        next_url = request.args.get("next") or "/dashboard"
        return redirect(next_url)

    return render_template("auth/login.html", form=form)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        display_name = form.display_name.data.strip() if form.display_name.data else email.split("@")[0]

        user = User(email=email, display_name=display_name)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        login_user(user)

        AuditLog.log(
            action="register",
            user_id=user.id,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

        notify.send_welcome_email(user.email, user.display_name or user.email.split("@")[0])
        flash("Account created! Let's set up your preferences.", "success")
        return redirect("/onboarding")

    return render_template("auth/register.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    AuditLog.log(
        action="logout",
        user_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    logout_user()
    flash("You have been logged out.", "info")
    return redirect("/")


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password_request():
    form = ResetPasswordRequestForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            serializer = URLSafeTimedSerializer(current_app.config.get("SECRET_KEY"))
            token = serializer.dumps(user.email)
            reset_link = url_for("auth.reset_password", token=token, _external=True)

            try:
                if current_app.config.get("MAIL_USERNAME") and current_app.config.get("MAIL_PASSWORD"):
                    msg = Message(
                        subject="Reset your UniversalDL password",
                        recipients=[user.email],
                        body=f"Use this link to reset your password: {reset_link}",
                    )
                    mail.send(msg)
                else:
                    current_app.logger.warning("Mail is not configured. Reset link: %s", reset_link)
            except Exception as exc:
                current_app.logger.warning("Failed to send reset email: %s", exc)
                current_app.logger.info("Reset link: %s", reset_link)

        flash("If that email exists, a reset link has been sent.", "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form, step=1)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    serializer = URLSafeTimedSerializer(current_app.config.get("SECRET_KEY"))

    try:
        email = serializer.loads(token, max_age=3600)
    except SignatureExpired:
        flash("Your reset link has expired. Please request a new one.", "warning")
        return redirect(url_for("auth.reset_password_request"))
    except BadSignature:
        flash("Invalid reset link. Please request a new one.", "danger")
        return redirect(url_for("auth.reset_password_request"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Invalid reset link. Please request a new one.", "danger")
        return redirect(url_for("auth.reset_password_request"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form, step=2, token=token)
