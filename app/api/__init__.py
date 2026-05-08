from flask import Blueprint, after_this_request, current_app, request

from app.extensions import csrf


api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


@api_bp.before_request
def api_before_request():
	current_app.logger.debug(
		"API request: %s %s from %s",
		request.method,
		request.path,
		request.remote_addr,
	)
	@after_this_request
	def _force_json_content_type(response):
		response.headers["Content-Type"] = "application/json"
		return response


@api_bp.after_request
def after_api_request(response):
	from app.api.auth import log_api_usage

	response = log_api_usage(response)
	response.headers["Content-Type"] = "application/json"
	return response


csrf.exempt(api_bp)


from app.api import routes  # noqa: E402,F401
