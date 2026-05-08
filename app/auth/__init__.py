from flask import Blueprint


auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")

from app.auth import oauth, routes  # noqa: E402,F401
