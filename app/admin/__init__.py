from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user


admin_bp = Blueprint(
	"admin",
	__name__,
	template_folder="../../templates/admin",
	url_prefix="/admin",
)


@admin_bp.before_request
def enforce_admin_access():
	if not current_user.is_authenticated:
		return redirect(url_for("auth.login", next=request.url))
	if not current_user.is_admin:
		abort(403)
	return None


@admin_bp.errorhandler(403)
def admin_forbidden(_error):
	return (
		render_template(
			"errors/403.html",
			message="You don't have permission to access the admin panel.",
		),
		403,
	)


from app.admin import routes  # noqa: E402,F401
