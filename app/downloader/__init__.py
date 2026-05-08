from flask import Blueprint


downloader_bp = Blueprint("downloader", __name__)


from app.downloader import routes  # noqa: E402,F401
