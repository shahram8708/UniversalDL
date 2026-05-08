import os
import random
import secrets
import uuid
from datetime import datetime, timedelta

import sentry_sdk
from dotenv import load_dotenv
from flask_cors import CORS
from flask import Flask, g, has_request_context, render_template, session
from flask_login import current_user
from sentry_sdk.integrations.flask import FlaskIntegration

from app.config import config_by_name
from app.celery_app import init_celery
from app.extensions import bcrypt, csrf, db, limiter, login_manager, mail, migrate
from app.models import BlogPost, PlatformExtractor, User
from app.auth.oauth import init_oauth


_REQUIRED_ENV_VARS = [
    "SECRET_KEY",
    "FLASK_ENV",
    "PORT",
    "DATABASE_URL",
    "REDIS_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "MAIL_SERVER",
    "MAIL_PORT",
    "MAIL_USERNAME",
    "MAIL_PASSWORD",
    "MAIL_DEFAULT_SENDER",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "TEMP_DOWNLOAD_DIR",
    "MAX_FILE_AGE_SECONDS",
    "SENTRY_DSN",
    "PROXY_PROVIDER_URL",
    "PROXY_PROVIDER_KEY",
    "PAGERDUTY_INTEGRATION_KEY",
    "APP_DOMAIN",
]


def format_bytes_filter(size_bytes: int) -> str:
    if not size_bytes:
        return "0 B"
    size = float(size_bytes)
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / (1024 ** 2):.1f} MB"
    return f"{size / (1024 ** 3):.2f} GB"


def format_number_filter(value) -> str:
    if value is None:
        return "0"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def get_relative_time_filter(dt) -> str:
    if not dt:
        return "Never"
    if getattr(dt, "tzinfo", None):
        now = datetime.now(dt.tzinfo)
    else:
        now = datetime.utcnow()
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "Just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago"
    days = hours // 24
    if days < 7:
        return f"{days} days ago"
    return dt.strftime("%b %d, %Y")


def _warn_missing_env(app):
    for key in _REQUIRED_ENV_VARS:
        if not os.environ.get(key):
            app.logger.warning("Missing environment variable: %s", key)


def create_app():
    load_dotenv()

    base_dir = os.path.abspath(os.path.dirname(__file__))
    template_dir = os.path.abspath(os.path.join(base_dir, "..", "templates"))
    static_dir = os.path.abspath(os.path.join(base_dir, "..", "static"))

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

    app.jinja_env.filters["format_bytes"] = format_bytes_filter
    app.jinja_env.filters["relative_time"] = get_relative_time_filter
    app.jinja_env.filters["format_number"] = format_number_filter
    app.jinja_env.globals["format_bytes"] = format_bytes_filter
    app.jinja_env.globals["get_relative_time"] = get_relative_time_filter

    env_name = os.environ.get("FLASK_ENV", "development")
    config_class = config_by_name.get(env_name, config_by_name["development"])
    app.config.from_object(config_class)

    CORS(
        app,
        resources={
            r"/api/v1/*": {
                "origins": [
                    r"chrome-extension://.*",
                    "http://localhost:5000",
                    "http://127.0.0.1:5000",
                    r"http://localhost:\d+",
                    r"http://127\.0\.0\.1:\d+",
                    "https://universaldl.onrender.com",
                    r"https://.*\.universaldl\.onrender.com",
                ],
                "methods": ["GET", "POST", "DELETE", "OPTIONS"],
                "allow_headers": [
                    "Content-Type",
                    "Authorization",
                    "X-CSRFToken",
                    "X-Extension-Version",
                ],
                "max_age": 600,
            },
            r"/download/analyze": {
                "origins": [
                    r"chrome-extension://.*",
                    "http://localhost:5000",
                    "http://127.0.0.1:5000",
                    r"http://localhost:\d+",
                    r"http://127\.0\.0\.1:\d+",
                    "https://universaldl.onrender.com",
                    r"https://.*\.universaldl\.onrender.com",
                ],
                "methods": ["GET", "POST", "DELETE", "OPTIONS"],
                "allow_headers": [
                    "Content-Type",
                    "Authorization",
                    "X-CSRFToken",
                    "X-Extension-Version",
                ],
                "max_age": 600,
            },
            r"/download/start": {
                "origins": [
                    r"chrome-extension://.*",
                    "http://localhost:5000",
                    "http://127.0.0.1:5000",
                    r"http://localhost:\d+",
                    r"http://127\.0\.0\.1:\d+",
                    "https://universaldl.onrender.com",
                    r"https://.*\.universaldl\.onrender.com",
                ],
                "methods": ["GET", "POST", "DELETE", "OPTIONS"],
                "allow_headers": [
                    "Content-Type",
                    "Authorization",
                    "X-CSRFToken",
                    "X-Extension-Version",
                ],
                "max_age": 600,
            },
            r"/download/cancel/.*": {
                "origins": [
                    r"chrome-extension://.*",
                    "http://localhost:5000",
                    "http://127.0.0.1:5000",
                    r"http://localhost:\d+",
                    r"http://127\.0\.0\.1:\d+",
                    "https://universaldl.onrender.com",
                    r"https://.*\.universaldl\.onrender.com",
                ],
                "methods": ["GET", "POST", "DELETE", "OPTIONS"],
                "allow_headers": [
                    "Content-Type",
                    "Authorization",
                    "X-CSRFToken",
                    "X-Extension-Version",
                ],
                "max_age": 600,
            },
        },
        supports_credentials=False,
    )

    _warn_missing_env(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)
    bcrypt.init_app(app)
    init_celery(app)

    if app.config.get("SENTRY_DSN"):
        sentry_sdk.init(dsn=app.config.get("SENTRY_DSN"), integrations=[FlaskIntegration()])

    init_oauth(app)

    from app.auth import auth_bp
    from app.main import main_bp
    from app.downloader import downloader_bp
    from app.dashboard import dashboard_bp
    from app.admin import admin_bp
    from app.api import api_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(main_bp)
    app.register_blueprint(downloader_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        if not user_id:
            return None
        try:
            user_uuid = uuid.UUID(str(user_id))
        except (ValueError, TypeError):
            return None
        return User.query.get(user_uuid)

    @app.context_processor
    def inject_globals():
        if not hasattr(g, "request_cache"):
            g.request_cache = {}
        if "platform_list" not in g.request_cache:
            g.request_cache["platform_list"] = (
                PlatformExtractor.query.filter_by(is_enabled=True)
                .order_by(PlatformExtractor.display_name)
                .all()
            )
        user = current_user
        is_admin = bool(getattr(user, "is_authenticated", False) and getattr(user, "is_admin", False))
        theme = session.get("theme", "light") if has_request_context() else "light"
        return {
            "current_year": datetime.now().year,
            "platform_list": g.request_cache["platform_list"],
            "app_version": app.config.get("APP_VERSION"),
            "is_admin": is_admin,
            "theme": theme,
        }

    @app.context_processor
    def inject_csp_nonce():
        return {"csp_nonce": g.get("csp_nonce", "")}

    @app.template_filter("timeago")
    def timeago(value):
        if not value:
            return "just now"
        if getattr(value, "tzinfo", None):
            now = datetime.now(value.tzinfo)
        else:
            now = datetime.utcnow()
        diff = now - value
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} minute" + ("s" if minutes != 1 else "") + " ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hour" + ("s" if hours != 1 else "") + " ago"
        days = hours // 24
        if days < 30:
            return f"{days} day" + ("s" if days != 1 else "") + " ago"
        months = days // 30
        if months < 12:
            return f"{months} month" + ("s" if months != 1 else "") + " ago"
        years = months // 12
        return f"{years} year" + ("s" if years != 1 else "") + " ago"

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        return render_template("errors/500.html"), 500

    @app.before_request
    def set_csp_nonce():
        if not getattr(g, "csp_nonce", None):
            g.csp_nonce = secrets.token_urlsafe(16)

    @app.after_request
    def add_security_headers(response):
        is_sse = response.mimetype == "text/event-stream"
        nonce = g.get("csp_nonce") or secrets.token_urlsafe(16)
        g.csp_nonce = nonce
        if not is_sse:
            csp = (
                "default-src 'self'; "
                f"script-src 'self' 'nonce-{nonce}' https://checkout.razorpay.com "
                "https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
                "https://fonts.googleapis.com https://fonts.gstatic.com; "
                "img-src 'self' data: https: blob:; "
                "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
                "connect-src 'self' https://api.razorpay.com https://cdn.jsdelivr.net "
                "https://fonts.googleapis.com https://fonts.gstatic.com "
                "https://images.unsplash.com https://checkout.razorpay.com "
                "chrome-extension:; "
                "frame-src https://api.razorpay.com; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self';"
            )
            response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(self \"https://checkout.razorpay.com\")"
        )
        if os.environ.get("FLASK_ENV") == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        return response

    def seed_initial_data():
        if not User.query.first():
            admin = User(
                email="admin@universaldl.com",
                display_name="Admin",
                is_admin=True,
                plan="enterprise",
                is_onboarded=True,
            )
            admin.set_password("Admin@123456")
            db.session.add(admin)

        if not PlatformExtractor.query.first():
            platforms = [
                {
                    "platform_id": "youtube",
                    "display_name": "YouTube",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.youtube",
                },
                {
                    "platform_id": "tiktok",
                    "display_name": "TikTok",
                    "requires_headless": True,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.tiktok",
                },
                {
                    "platform_id": "instagram",
                    "display_name": "Instagram",
                    "requires_headless": True,
                    "requires_proxy": True,
                    "extractor_module": "app.services.extractors.instagram",
                },
                {
                    "platform_id": "twitter",
                    "display_name": "Twitter",
                    "requires_headless": True,
                    "requires_proxy": True,
                    "extractor_module": "app.services.extractors.twitter",
                },
                {
                    "platform_id": "reddit",
                    "display_name": "Reddit",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.reddit",
                },
                {
                    "platform_id": "twitch",
                    "display_name": "Twitch",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.twitch",
                },
                {
                    "platform_id": "vimeo",
                    "display_name": "Vimeo",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.vimeo",
                },
                {
                    "platform_id": "soundcloud",
                    "display_name": "SoundCloud",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.soundcloud",
                },
                {
                    "platform_id": "bilibili",
                    "display_name": "Bilibili",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.bilibili",
                },
                {
                    "platform_id": "facebook",
                    "display_name": "Facebook",
                    "requires_headless": True,
                    "requires_proxy": True,
                    "extractor_module": "app.services.extractors.facebook",
                },
                {
                    "platform_id": "dailymotion",
                    "display_name": "Dailymotion",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.dailymotion",
                },
                {
                    "platform_id": "spotify",
                    "display_name": "Spotify",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.spotify",
                },
                {
                    "platform_id": "linkedin",
                    "display_name": "LinkedIn",
                    "requires_headless": True,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.linkedin",
                },
                {
                    "platform_id": "pinterest",
                    "display_name": "Pinterest",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.pinterest",
                },
                {
                    "platform_id": "podcast_rss",
                    "display_name": "Podcast RSS Feeds",
                    "is_enabled": True,
                    "requires_headless": False,
                    "requires_proxy": False,
                    "success_rate_7d": 98.0,
                    "extractor_module": "app.extractors.podcast_rss.PodcastRSSExtractor",
                    "config_json": {
                        "test_url": "https://feeds.simplecast.com/54nAGcIl",
                        "description": "RSS/Atom podcast feeds and individual episode pages",
                    },
                },
                {
                    "platform_id": "netflix",
                    "display_name": "Netflix",
                    "is_enabled": True,
                    "requires_headless": False,
                    "requires_proxy": False,
                    "success_rate_7d": 0.0,
                    "failure_reason": "DRM-protected — downloads intentionally blocked",
                    "extractor_module": "app.extractors.netflix.NetflixExtractor",
                    "config_json": {
                        "test_url": "https://www.netflix.com/watch/70143836",
                        "drm_platform": True,
                        "drm_type": "Widevine L1",
                        "description": "Netflix always returns DRM error by design",
                    },
                },
                {
                    "platform_id": "coursera",
                    "display_name": "Coursera",
                    "is_enabled": True,
                    "requires_headless": True,
                    "requires_proxy": False,
                    "success_rate_7d": 70.0,
                    "extractor_module": "app.extractors.coursera.CourseraExtractor",
                    "config_json": {
                        "test_url": "https://www.coursera.org/lecture/python/preview",
                        "preview_only": True,
                        "description": "Lecture preview pages can expose media, but guided project pages usually require enrollment",
                    },
                },
                {
                    "platform_id": "behance",
                    "display_name": "Behance",
                    "is_enabled": True,
                    "requires_headless": False,
                    "requires_proxy": False,
                    "success_rate_7d": 92.0,
                    "extractor_module": "app.extractors.behance.BehanceExtractor",
                    "config_json": {
                        "test_url": "https://www.behance.net/gallery/188756185/Test",
                        "description": "Images, galleries, and native videos from Behance projects",
                    },
                },
                {
                    "platform_id": "imgur",
                    "display_name": "Imgur",
                    "is_enabled": True,
                    "requires_headless": False,
                    "requires_proxy": False,
                    "success_rate_7d": 97.0,
                    "extractor_module": "app.extractors.imgur.ImgurExtractor",
                    "config_json": {
                        "test_url": "https://imgur.com/gallery/9BHBK",
                        "api_client_id": "546c25a59c58ad7",
                        "description": "Single images, GIFs, MP4 videos, and albums from Imgur",
                    },
                },
                {
                    "platform_id": "generic",
                    "display_name": "Generic",
                    "requires_headless": False,
                    "requires_proxy": False,
                    "extractor_module": "app.services.extractors.generic",
                },
            ]
            for platform in platforms:
                db.session.add(
                    PlatformExtractor(
                        platform_id=platform["platform_id"],
                        display_name=platform["display_name"],
                        is_enabled=platform.get("is_enabled", True),
                        requires_headless=platform["requires_headless"],
                        requires_proxy=platform["requires_proxy"],
                        extractor_module=platform["extractor_module"],
                        success_rate_7d=platform.get("success_rate_7d", 100.0),
                        failure_reason=platform.get("failure_reason"),
                        config_json=platform.get("config_json"),
                    )
                )

        if not BlogPost.query.first():
            now = datetime.utcnow()
            posts = [
                BlogPost(
                    slug="download-youtube-videos-4k-guide",
                    title="How to Download YouTube Videos in 4K Quality (2026 Guide)",
                    excerpt=(
                        "This guide explains how to get the highest quality from YouTube, "
                        "including 4K and HDR, with the right format and subtitle options. "
                        "Learn supported URL types, playlist workflows, and quality tips."
                    ),
                    content="""<h2>Why 4K matters for YouTube downloads</h2>
<p>YouTube has expanded 4K coverage across creators, courses, and documentaries, but the best quality is not always the default. UniversalDL analyzes your link and reveals the highest quality streams available, then lets you choose the exact format you want. The Pro plan unlocks 4K and 8K selections for true archival quality, while the Free plan still delivers up to 1080p for everyday use. This guide walks through the full workflow so you can get the best result consistently.</p>
<h2>Supported YouTube URL types</h2>
<p>UniversalDL recognizes the common YouTube URL patterns automatically, which means you do not need to select the platform manually. Standard watch links like https://youtube.com/watch?v=VIDEO_ID work, as do shortened youtu.be links. Shorts are supported with the /shorts/VIDEO_ID path, and playlists are supported with /playlist?list=LIST_ID. You can also paste channel or user video links if they resolve to a specific video page. The parser handles extra parameters like t=, list=, and index= without breaking.</p>
<h2>Choosing quality the right way</h2>
<p>After you paste a link, UniversalDL analyzes available qualities. If the source offers 4K, you will see it in the quality selector. Pro users can select 4K or 8K when the content provides it. If a video tops out at 1080p, the selector will show that as the maximum. For archival downloads, pick the highest available resolution and select the format that preserves the codec. For quick sharing, pick 1080p or 720p to reduce file size and download time.</p>
<h2>Formats and codecs explained</h2>
<p>YouTube streams typically use VP9 or AV1 for high resolution and H.264 for lower tiers. UniversalDL lets you save in MP4, MKV, or WebM and can also extract audio only. Use MP4 for broad compatibility, MKV for archival work with multiple tracks, and WebM if you want the native stream with minimal conversion. Audio only downloads can be exported as MP3, M4A, or FLAC. Server side conversion means your device does not need to run FFmpeg locally.</p>
<h2>Subtitles, chapters, and metadata</h2>
<p>For YouTube, you can download subtitles in dozens of languages. UniversalDL supports both manually authored and auto generated tracks, and you can choose to embed them in the video or download them as separate files. Chapters are preserved when available, so long videos retain navigation markers in compatible players. Metadata injection is also supported, which means title, creator, and thumbnails are written into the output file so your media library stays organized.</p>
<h2>Playlists and batch downloads</h2>
<p>Playlists are perfect for courses, lecture series, and multi part documentaries. Paste the playlist link and switch to batch mode to queue every item at once. Pro users can submit larger batches and the queue will process multiple items in parallel. If you need a few items from a long list, you can still run individual analyses and choose the exact quality for each. Batch mode is also ideal for archiving playlists on a schedule using the subscription monitor feature.</p>
<h2>Age restricted or private content</h2>
<p>UniversalDL does not log into YouTube accounts, which means age restricted, private, or members only content is not supported. If a video requires a login prompt or payment, the extractor will fail by design. This keeps the platform compliant and protects user privacy. For publicly accessible content, the system uses direct network extraction without API keys, which is why it remains reliable even as official APIs change.</p>
<h2>Tips for getting the best quality</h2>
<p>When 4K is available, choose the highest resolution and a format that preserves the original codec. VP9 and AV1 streams often provide better quality at smaller sizes compared to H.264. If you plan to edit the video later, MKV is a great container because it can hold multiple subtitle tracks and chapter data. For the smallest file sizes, choose 1080p or 720p and a standard MP4 container. A stable internet connection helps the analyzer list more quality options.</p>
<h2>Final checklist</h2>
<p>Use the right URL type, select the highest quality available, and pick the best format for your device or archive. UniversalDL keeps your files local and does not store your media, so your download is yours immediately. Always respect the rights of creators and the policies of the platforms you use. With these steps, you can consistently download YouTube videos in 4K when available and keep your library organized for the long term.</p>
<h2>Troubleshooting and common issues</h2>
<p>If you do not see 4K as an option, the video may not be available in that resolution, or the platform may be serving a lower stream in your region. Try again later, or compare the same video on desktop to confirm the availability of the 4K indicator. If the download fails during analysis, it is often because the link points to a removed or unlisted video. Always verify that the URL opens in your browser before submitting it to UniversalDL.</p>
<p>When you see multiple 4K options, pick the one that matches your workflow. For editing and archiving, MKV with subtitles embedded is a good default. For fast playback on mobile devices, MP4 is more compatible. For audio only, use FLAC when you want lossless quality, or MP3 for a smaller file size. If you need subtitles, choose your language before starting the download because subtitles are embedded during processing.</p>
<h2>Organizing a long term YouTube library</h2>
<p>A consistent naming scheme saves time later. Use a folder structure by channel, then by year or playlist. Keep a small text file with the source URL and the download date for each item so you can verify provenance. For classes and lectures, save the original title and add the speaker name or course module number. UniversalDL metadata injection helps, but a clear folder layout makes your archive easier to search.</p>
<h2>Quality and bandwidth tradeoffs</h2>
<p>4K video is large, so plan for storage. A single hour long 4K video can be several gigabytes depending on the codec. If you are on a metered connection, consider 1080p for casual viewing and reserve 4K for long term archival items. UniversalDL lets you pick the quality per download, so you can mix formats based on importance and storage constraints.</p>
""",
                    thumbnail_url="https://images.unsplash.com/photo-1469474968028-56623f02e42e",
                    author="UniversalDL Team",
                    category="YouTube",
                    published_at=now - timedelta(days=4),
                    is_published=True,
                    meta_description=(
                        "Learn how to download YouTube videos in 4K with the right URL types, "
                        "format choices, subtitles, and playlist tips using UniversalDL."
                    ),
                    view_count=random.randint(120, 3200),
                ),
                BlogPost(
                    slug="download-tiktok-without-watermark",
                    title="Downloading TikTok Videos Without Watermark: The Complete Guide",
                    excerpt=(
                        "TikTok watermark removal is possible when you use the correct link and "
                        "quality workflow. This guide covers supported URL formats, slideshows, "
                        "batch downloads, and mobile share sheet tips."
                    ),
                    content="""<h2>Understanding the TikTok watermark</h2>
<p>TikTok adds a moving watermark to videos to protect creators and to keep the platform brand visible when clips are shared. For personal archiving, you may prefer a clean copy for editing or research. UniversalDL retrieves the original media stream and uses a no watermark CDN endpoint when it is available. That gives you a cleaner result without needing sketchy third party tools. The process is still respectful: it only works for public content and does not bypass private account protections.</p>
<h2>Supported TikTok URLs</h2>
<p>You can paste a direct video link like https://www.tiktok.com/@user/video/1234567890 and UniversalDL will recognize it immediately. Short links also work, as do links shared from the TikTok app. If you share from mobile, the Web Share Target feature opens UniversalDL and pre fills the URL automatically. For older content, the video id may be embedded in query parameters. UniversalDL normalizes these variations so you do not have to clean the link manually.</p>
<h2>How no watermark downloads work</h2>
<p>The extractor looks for the clean stream URL that TikTok provides for public playback. When it is present, UniversalDL uses that source and delivers a file without the moving watermark overlay. If TikTok only exposes a watermarked version for a specific clip, UniversalDL will still provide the best available file and clearly label the quality. This transparency helps you decide whether to archive the original or wait for a higher quality source later.</p>
<h2>Slideshows, photos, and mixed posts</h2>
<p>TikTok posts are not always video only. Slideshows and photo mode posts include multiple images with background audio. UniversalDL supports these by packaging the images in order and including the audio track when available. For long image sets, batch mode is helpful because you can queue multiple posts at once and let the system process them in parallel. This is ideal for collecting visual references or archiving creator portfolios.</p>
<h2>Quality choices and formats</h2>
<p>Most TikTok videos are optimized for mobile, which means resolutions can vary. UniversalDL still offers multiple quality options when the stream provides them and lets you save as MP4, MKV, or audio only formats. If you plan to edit the clip, MKV can keep metadata intact. For fast sharing, MP4 is the most compatible. Audio only is useful for clips where you only need the soundtrack or spoken content.</p>
<h2>Batch downloads for collections</h2>
<p>Creators often post multi part series or collections. Use batch mode to paste a list of URLs from a collection or a saved list. Pro users can submit larger batches and keep the queue running while you work on other tasks. Each item has its own progress status, and if a particular clip fails, it does not block the rest of the batch. This makes it practical to archive dozens of posts with consistent settings.</p>
<h2>Mobile workflow with PWA share sheet</h2>
<p>On mobile, the fastest workflow is to install the UniversalDL PWA and use the share sheet. From TikTok, tap Share, select UniversalDL, and the app will open with the URL pre filled. You can then select quality and format just like on desktop. This flow avoids copying and pasting and reduces errors in long URLs. It also works offline for queued requests and will retry when connectivity returns.</p>
<h2>Legal and ethical considerations</h2>
<p>TikTok content is still subject to creator rights and local copyright rules. UniversalDL is built for personal archiving, research, and education, not for re upload or commercial redistribution. If you are collecting clips for a project, always credit the creator and ensure you have permission to use the material. Public availability does not mean public ownership, so treat downloads as a reference library rather than a content source for publishing.</p>
<h2>Final tips</h2>
<p>Use the direct video link whenever possible, choose the highest quality offered, and store your files with clear names. For large projects, batch mode and the subscription monitor can save hours. The cleanest no watermark results usually come from newly posted public videos. UniversalDL makes the workflow consistent, fast, and safe, so you can archive TikTok content without risky browser extensions.</p>
<h2>Editing and reuse considerations</h2>
<p>If you plan to edit TikTok clips, download the highest available quality and save in a format that preserves the source data. MKV is a good choice for editing and archiving because it can hold metadata and multiple tracks. MP4 is better when you need a file that plays on any device. For research and reference, focus on preserving the original caption text and the upload date in a separate notes file.</p>
<h2>Managing large collections</h2>
<p>When you are working with hundreds of clips, batch mode is only the first step. Organize your files by creator or campaign and include the original video ID in the filename so you can trace the source later. If you are capturing clips for a time sensitive event, consider running multiple batches on a schedule to keep the archive fresh. The subscription monitor is useful when you need to track updates from a small set of creators.</p>
<h2>Common errors and how to avoid them</h2>
<p>Most failures are caused by private content or removed posts. Always verify that the clip opens in a regular browser without logging in. If you encounter a temporary error, retry after a few minutes. TikTok may throttle requests when there is high traffic, so spreading downloads across time reduces failures. UniversalDL automatically retries in many cases, but a short delay can improve success rates.</p>
""",
                    thumbnail_url="https://images.unsplash.com/photo-1500530855697-b586d89ba3ee",
                    author="UniversalDL Team",
                    category="TikTok",
                    published_at=now - timedelta(days=8),
                    is_published=True,
                    meta_description=(
                        "Download TikTok videos without watermark using supported URL types, "
                        "batch tools, and the mobile share sheet with UniversalDL."
                    ),
                    view_count=random.randint(200, 4600),
                ),
                BlogPost(
                    slug="archiving-twitter-x-content",
                    title="Archiving Twitter/X Content Before It Disappears",
                    excerpt=(
                        "Tweets can vanish without warning. This guide shows how to archive "
                        "Twitter or X content, including videos, images, and full threads, "
                        "using privacy friendly workflows."
                    ),
                    content="""<h2>Why archiving Twitter or X matters</h2>
<p>Twitter, now known as X, moves fast. Accounts are suspended, posts are deleted, and links break. For journalists, researchers, and educators, losing a source can mean losing context. Archiving preserves a snapshot for later reference and citation. UniversalDL captures video, images, and the associated text content where possible so you can keep evidence together. The goal is not to republish content but to preserve a stable record of public information.</p>
<h2>What UniversalDL can archive</h2>
<p>UniversalDL supports public tweet URLs and extracts media files directly. Videos are downloaded with audio when the platform provides separate streams, and image posts can be saved at full resolution. The system also captures metadata like the tweet text, author name, and post time when available, which helps with later attribution. If a tweet includes multiple images, they are saved as a grouped set so you can keep them organized.</p>
<h2>Archiving threads and long conversations</h2>
<p>Threads often provide the most important context, but they are easy to lose when a single tweet is deleted. To archive a thread, collect each tweet URL and use batch mode. This saves the media for each item and keeps the URLs in a single batch history entry. For long threads, you can paste the links in a text file and upload it to the batch downloader. This approach reduces manual effort and helps preserve a narrative sequence.</p>
<h2>Rate limits and reliability tips</h2>
<p>Like most platforms, X can throttle requests when many downloads happen in a short time. UniversalDL automatically spreads work across workers and retries when it encounters temporary errors. If you are archiving a large set, start with smaller batches and monitor the platform status page. If a platform shows a degraded state, wait and retry later. Using the proxy pool can also improve reliability in regions where access is limited.</p>
<h2>Anonymous mode for sensitive research</h2>
<p>Some archives are privacy sensitive. UniversalDL offers anonymous mode, which avoids storing the URL in your download history. This is useful for investigations or sensitive research where you do not want a list of URLs saved in your account. The download still works as normal, but the system reduces retention of link level data. You can enable anonymous mode in Settings at any time and use it per download.</p>
<h2>Best practices for evidence preservation</h2>
<p>For evidence, record both the media and the surrounding context. Save the tweet text, images, and any quoted tweets as separate items in the same batch. If a clip includes multiple parts, download each segment rather than relying on a single remix. Keep a written log that records the URL, date, and reason for archiving. UniversalDL provides timestamps in the download history, which can help you document when the capture was performed.</p>
<h2>Ethical use and platform rules</h2>
<p>Archiving should respect creator rights and platform policies. Use the content for research, personal reference, or documentation, not for redistribution. If you publish derived work, credit the source and provide context. UniversalDL is built to keep your archive local and does not host the files, which reduces privacy risks. Ultimately, you are responsible for ensuring your use complies with local laws and the platform terms of service.</p>
<h2>Summary</h2>
<p>Twitter or X content can disappear at any time, which makes archiving important for historians, journalists, and everyday users. UniversalDL provides a reliable workflow for capturing media, metadata, and thread context without relying on official APIs. With batch downloads and anonymous mode, you can build a trustworthy archive while protecting your privacy. Use these tools responsibly and keep a clean record of your sources.</p>
<h2>Archiving with provenance</h2>
<p>For evidence grade archives, provenance matters. Record the original URL, the date of download, and a short description of why the content matters. If you are working on a case file, store a local PDF capture of the tweet page alongside the media. UniversalDL focuses on the media itself, so pairing it with a textual record helps preserve context. A simple CSV or spreadsheet is enough for most workflows.</p>
<h2>Threads and quoted tweets</h2>
<p>Quoted tweets and replies often add essential context. When you archive a thread, include quoted tweets that appear within it and save the media from each item. A common mistake is to archive only the first tweet, which leaves out replies that change the meaning. Use batch mode and keep the URLs in order. You can also annotate the batch with a short label to make it easier to find later.</p>
<h2>Handling sensitive content</h2>
<p>If you are archiving sensitive or privacy related content, enable anonymous mode and restrict access to your archive. UniversalDL avoids storing URLs when anonymous mode is enabled, but you should still manage your local storage carefully. Limit file sharing, and consider encrypting your archive if it includes personal or sensitive information. Respect privacy while preserving evidence.</p>
""",
                    thumbnail_url="https://images.unsplash.com/photo-1461749280684-dccba630e2f6",
                    author="UniversalDL Team",
                    category="Twitter",
                    published_at=now - timedelta(days=12),
                    is_published=True,
                    meta_description=(
                        "Archive Twitter or X videos and images safely with batch downloads, "
                        "anonymous mode, and evidence friendly workflows."
                    ),
                    view_count=random.randint(80, 4200),
                ),
                BlogPost(
                    slug="download-instagram-reels-stories",
                    title="How to Download Instagram Reels and Stories",
                    excerpt=(
                        "Instagram posts, reels, and stories require a careful workflow. "
                        "This guide explains supported URL formats, quality tips, and how "
                        "to use batch mode for multiple reels."
                    ),
                    content="""<h2>Instagram downloads require a careful approach</h2>
<p>Instagram changes its delivery logic often, and many pages are rendered with JavaScript. UniversalDL uses a headless browser when required, which allows it to capture public content reliably without relying on private APIs. This is why Instagram is labeled as JS rendered in the platform list. The workflow is still simple: paste the URL, let the analyzer detect the content, and choose your format and quality. The system handles the heavy lifting automatically.</p>
<h2>Supported URL formats</h2>
<p>UniversalDL supports public post URLs in the /p/ format, reels in the /reel/ path, and stories in the /stories/ path when the content is public and visible. You can paste a direct link copied from the Instagram app or the web interface. For reels, the extractor detects the video stream and provides the highest available quality. For posts with multiple images, the downloader will package each image with its original resolution when available.</p>
<h2>Public content only</h2>
<p>Private accounts and follower only content are not supported. UniversalDL does not log in to Instagram on your behalf, which keeps your account data private and reduces risk. If a link redirects to a login prompt, the extractor will fail and clearly report that the content is private. This is an intentional limitation. For public accounts, the downloader remains reliable even as Instagram updates page layouts.</p>
<h2>Quality and format tips</h2>
<p>Instagram optimizes for mobile playback, so resolution can vary. UniversalDL shows the best available quality and lets you choose MP4 or MKV for video. If you only need the audio, you can extract MP3 or M4A. For archives, MKV keeps metadata clean and supports subtitle tracks if they exist. For quick sharing, MP4 is the safest choice across devices. Always check the preview thumbnail to ensure you are downloading the correct item.</p>
<h2>Carousel posts and galleries</h2>
<p>Carousel posts are a mix of images and videos within a single post. UniversalDL detects each slide and downloads them in order. This is useful for saving tutorials, design references, or galleries. If a carousel includes both image and video items, the downloader saves each file separately so you can organize them more easily. The batch downloader can also handle multiple carousel links in a single queue.</p>
<h2>Batch mode for multiple reels</h2>
<p>If you need to save a collection of reels, batch mode is the fastest way. Paste a list of URLs or upload a text file and let the queue process them in parallel. Pro users get higher batch limits and faster processing. Each item in the queue has a separate progress indicator, which makes it easy to see where a failed download occurred. You can retry a failed item without restarting the entire batch.</p>
<h2>Mobile share workflow</h2>
<p>On mobile, install the UniversalDL PWA and use the share sheet. From Instagram, tap Share, pick UniversalDL, and the download page opens with the URL pre filled. This avoids copy and paste errors and makes it easier to archive content while you browse. If you are temporarily offline, the URL can be queued and will retry once the connection is restored.</p>
<h2>Best practices and limitations</h2>
<p>For the best results, use the direct post or reel link and avoid links that include tracking parameters from third party tools. If a reel appears as a story preview, open it in the web view and copy the real URL. UniversalDL respects platform limitations and does not bypass private content. If you need content from a private account, request permission from the creator rather than trying to circumvent access controls.</p>
<h2>Summary</h2>
<p>Instagram downloads can be reliable when the workflow is designed to handle dynamic pages. UniversalDL does this with a headless approach and clear status reporting. Use the supported URL formats, pick the right format, and rely on batch mode for larger jobs. The result is a clean, safe archive of public reels, stories, and posts without relying on risky browser extensions.</p>
<h2>Story downloads and timing</h2>
<p>Stories expire quickly. If you want to archive a story, download it as soon as you discover it. Public stories can be saved while they are visible. After expiration, the link will return a not found error. If you are monitoring a creator, the subscription monitor can help you capture new stories faster, but remember that stories are ephemeral and may disappear before the next scheduled check.</p>
<h2>Audio and captions</h2>
<p>Instagram reels often use licensed audio. UniversalDL downloads the audio track when it is part of the public reel. If the platform removes audio due to licensing, the download will only include the video. For accessibility, captions are not always available on Instagram, so you may need to create your own transcript if you are archiving for educational purposes.</p>
<h2>Organizing your Instagram archive</h2>
<p>For large archives, create folders by creator and date. Use the post ID in filenames to avoid collisions. If you are archiving reels for research, include a short note with the caption text and hashtags. These details provide context later. UniversalDL keeps the file metadata clean, but a separate index of captions and topics will make your archive more searchable.</p>
""",
                    thumbnail_url="https://images.unsplash.com/photo-1487412720507-e7ab37603c6f",
                    author="UniversalDL Team",
                    category="Instagram",
                    published_at=now - timedelta(days=16),
                    is_published=True,
                    meta_description=(
                        "Download Instagram reels, stories, and posts using supported URLs, "
                        "batch mode, and mobile share sheet workflows with UniversalDL."
                    ),
                    view_count=random.randint(90, 3900),
                ),
                BlogPost(
                    slug="batch-download-podcast-episodes",
                    title="Batch Downloading Podcast Episodes: The Complete Automation Guide",
                    excerpt=(
                        "Podcast episodes disappear or move behind paywalls. Learn how to "
                        "batch download episodes from SoundCloud and Spotify, use subscriptions, "
                        "and automate with the REST API."
                    ),
                    content="""<h2>Why podcast archiving matters</h2>
<p>Podcast feeds change without warning. Episodes are removed, hosts switch platforms, and back catalogs can vanish behind paywalls. If you rely on a show for research or teaching, a stable archive matters. UniversalDL offers a repeatable workflow for downloading and organizing podcast episodes from supported platforms like SoundCloud and Spotify podcasts. The key is to use batch mode for initial captures and subscription monitoring for ongoing updates.</p>
<h2>SoundCloud podcast downloads</h2>
<p>SoundCloud hosts a large number of independent podcasts. UniversalDL extracts audio files and metadata from public tracks and playlists. For a full archive, paste the playlist link and use batch mode to queue every episode at once. Each item can be saved as MP3 or FLAC depending on your quality requirements. Metadata injection writes show title, episode name, and artwork into the file so your media library remains clean and searchable.</p>
<h2>Spotify podcast episodes</h2>
<p>Spotify hosts many free podcasts that are not DRM protected. UniversalDL supports these public podcast episodes, but it does not download DRM locked music. This means the platform remains compliant while still helping you archive legitimate podcast content. When a podcast has multiple seasons, you can pull episode URLs in a list and submit them in batch mode. Use descriptive file naming conventions to preserve season and episode order.</p>
<h2>RSS feeds and discovery</h2>
<p>If you already have the RSS feed, you can use it to build a list of episode URLs. UniversalDL focuses on platform URLs rather than direct file links, which keeps the workflow consistent. Many podcasts mirror their content on SoundCloud or other supported hosts, making it easy to batch download. If a show only publishes a feed with direct MP3 links, you can still download those files with the generic extractor as long as the URLs are public.</p>
<h2>Subscription monitor automation</h2>
<p>The subscription monitor is built for long term archiving. Add a podcast channel and choose hourly, daily, or weekly checks. UniversalDL tracks known content ids to avoid duplicate downloads and will only fetch new items. You can enable email notifications so you know when new episodes are added. This turns a manual task into an automated archive that stays current with minimal effort.</p>
<h2>Organizing your archive</h2>
<p>Large podcast collections can get messy fast. Use a folder structure like Show Name / Season / Episode. UniversalDL includes metadata injection and consistent filenames to help you sort and search. For researchers, you can store notes or transcripts alongside the audio file. For language learners, download episodes with clear titles so you can build curated listening lists by topic or difficulty.</p>
<h2>REST API workflows</h2>
<p>Pro users can automate downloads with the REST API. Use the analyze endpoint to verify a URL, then start a download job and poll the status. This is useful for integrating with Plex or Jellyfin, or for syncing archives to a NAS. Webhook callbacks can notify your automation script when a download completes. This turns UniversalDL into a reliable backend for podcast capture pipelines.</p>
<h2>Webhooks and post processing</h2>
<p>Once a download finishes, you may want to run a post processing job like normalizing audio, tagging, or moving files to long term storage. Webhooks provide a clean way to trigger those tasks. Combine them with a small automation script that renames files or updates a database. Because UniversalDL delivers files directly to your device or server, you remain in control of the final storage destination.</p>
<h2>Summary</h2>
<p>Podcast archiving is a long game. Use batch downloads for the initial capture, then rely on subscription monitoring and the API for ongoing automation. UniversalDL supports public podcast platforms and avoids DRM protected media, keeping the workflow compliant and reliable. With good organization and consistent metadata, your archive stays useful for years even as platforms change or episodes disappear.</p>
<h2>Building a resilient workflow</h2>
<p>A reliable archive is not just about downloading. It is also about verifying consistency. After a batch finishes, spot check a few episodes to confirm audio quality and metadata accuracy. If you are building a long term archive, schedule periodic validations to ensure that file metadata remains intact. UniversalDL keeps filenames and tags consistent, which makes this easier.</p>
<h2>Managing storage and backups</h2>
<p>Podcast audio can add up quickly. Store files on a dedicated drive and back up to a second location or cloud bucket that you control. If you are building a research archive, keep a checksum list so you can confirm files have not been corrupted. For personal archives, a simple weekly backup to an external drive is usually enough.</p>
<h2>Automation examples</h2>
<p>Common automation patterns include nightly batch pulls, weekly subscription checks, and webhook driven post processing. For example, after a download completes, your webhook can trigger a script that copies the file to a Plex library and updates a database. These workflows reduce manual work and make it easier to maintain a large, organized collection of audio.</p>
""",
                    thumbnail_url="https://images.unsplash.com/photo-1498050108023-c5249f4df085",
                    author="UniversalDL Team",
                    category="Audio",
                    published_at=now - timedelta(days=20),
                    is_published=True,
                    meta_description=(
                        "Batch download podcast episodes with SoundCloud and Spotify workflows, "
                        "subscription monitoring, and REST API automation using UniversalDL."
                    ),
                    view_count=random.randint(140, 5000),
                ),
            ]
            db.session.add_all(posts)

        db.session.commit()

        temp_dir = app.config.get("TEMP_DOWNLOAD_DIR")
        if temp_dir:
            os.makedirs(temp_dir, exist_ok=True)

        print("Database initialized and seeded.")

    with app.app_context():
        db.create_all()
        seed_initial_data()

    return app
