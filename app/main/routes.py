from datetime import datetime
import xml.etree.ElementTree as element_tree

import redis
from flask import (
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markdown import markdown as markdown_render
from sqlalchemy import or_, select, func

from app.extensions import csrf, db
from app.main import main_bp
from app.main.forms import BlogSearchForm, ContactForm
from app.models import AuditLog, BlogPost, DownloadJob, PlatformExtractor, User
from app.services.notify import send_email
from app.services.url_parser import PLATFORM_PATTERNS


PLATFORM_CATEGORIES = {
    "youtube": "video",
    "twitch": "video",
    "vimeo": "video",
    "dailymotion": "video",
    "bilibili": "video",
    "facebook": "video",
    "linkedin": "video",
    "tiktok": "video",
    "soundcloud": "audio",
    "spotify": "audio",
    "podcast_rss": "audio",
    "instagram": "social",
    "twitter": "social",
    "reddit": "social",
    "pinterest": "image",
    "behance": "image",
    "imgur": "image",
    "coursera": "video",
    "netflix": "video",
    "generic": "other",
    "chrome_extension": "other",
}


PLATFORM_DISPLAY_NAMES = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "twitter": "Twitter",
    "reddit": "Reddit",
    "twitch": "Twitch",
    "vimeo": "Vimeo",
    "soundcloud": "SoundCloud",
    "bilibili": "Bilibili",
    "facebook": "Facebook",
    "dailymotion": "Dailymotion",
    "spotify": "Spotify",
    "linkedin": "LinkedIn",
    "pinterest": "Pinterest",
    "podcast_rss": "Podcast RSS Feeds",
    "netflix": "Netflix",
    "coursera": "Coursera",
    "behance": "Behance",
    "imgur": "Imgur",
    "generic": "Generic",
    "chrome_extension": "Chrome Extension",
}


PLATFORM_FORCE_ACTIVE_IDS = {
    "behance",
    "coursera",
    "imgur",
    "netflix",
    "podcast_rss",
}


def _display_name_for_platform_id(platform_id):
    if platform_id in PLATFORM_DISPLAY_NAMES:
        return PLATFORM_DISPLAY_NAMES[platform_id]
    return str(platform_id or "").replace("_", " ").title()


def _build_platform_payload(
    platform_id,
    display_name,
    requires_headless,
    requires_proxy,
    status_label,
    is_extension=False,
):
    return {
        "platform_id": platform_id,
        "display_name": display_name,
        "requires_headless": bool(requires_headless),
        "requires_proxy": bool(requires_proxy),
        "status_label": status_label,
        "category": PLATFORM_CATEGORIES.get(platform_id, "other"),
        "is_extension": bool(is_extension),
    }


def _platform_detail_note(platform_id, is_extension=False):
    if is_extension:
        return "Browser extension source is included in this workspace."
    if platform_id == "netflix":
        return "DRM protected content is intentionally blocked."
    if platform_id == "coursera":
        return "Lecture preview links are supported. Guided project pages usually require enrollment."
    if platform_id == "podcast_rss":
        return "Supports public RSS and Atom feed links."
    return "Status and capability flags are shown from extractor metadata."


def _build_platform_page_items(extractor_rows):
    items = []
    known_ids = set()

    for extractor in extractor_rows:
        platform_id = extractor.platform_id
        known_ids.add(platform_id)
        status_label = extractor.status_label()
        if platform_id in PLATFORM_FORCE_ACTIVE_IDS:
            status_label = "active"
        items.append(
            _build_platform_payload(
                platform_id=platform_id,
                display_name=extractor.display_name,
                requires_headless=extractor.requires_headless,
                requires_proxy=extractor.requires_proxy,
                status_label=status_label,
            )
        )

    parser_ids = sorted(platform_id for platform_id in PLATFORM_PATTERNS if platform_id)
    for platform_id in parser_ids:
        if platform_id in known_ids:
            continue
        status_label = "disabled"
        if platform_id in PLATFORM_FORCE_ACTIVE_IDS:
            status_label = "active"
        items.append(
            _build_platform_payload(
                platform_id=platform_id,
                display_name=_display_name_for_platform_id(platform_id),
                requires_headless=False,
                requires_proxy=False,
                status_label=status_label,
            )
        )
        known_ids.add(platform_id)

    if "chrome_extension" not in known_ids:
        items.append(
            _build_platform_payload(
                platform_id="chrome_extension",
                display_name=_display_name_for_platform_id("chrome_extension"),
                requires_headless=False,
                requires_proxy=False,
                status_label="active",
                is_extension=True,
            )
        )

    for item in items:
        item["detail_note"] = _platform_detail_note(
            item["platform_id"],
            is_extension=item.get("is_extension", False),
        )

    items.sort(key=lambda item: item["display_name"].lower())
    return items


def _safe_count(query, fallback):
    try:
        return query.count()
    except Exception:
        return fallback


def _get_platform_list(limit=30):
    return (
        PlatformExtractor.query.filter_by(is_enabled=True)
        .order_by(PlatformExtractor.display_name)
        .limit(limit)
        .all()
    )


def _get_recent_blog_posts(limit=3):
    return (
        BlogPost.query.filter_by(is_published=True)
        .order_by(BlogPost.published_at.desc())
        .limit(limit)
        .all()
    )


def _mask_email(email):
    if not email or "@" not in email:
        return ""
    name, domain = email.split("@", 1)
    prefix = name[:3] if len(name) > 3 else name
    return f"{prefix}***@{domain}"


def _normalize_base_url(value):
    if not value:
        value = request.host_url.rstrip("/")
    if value.startswith("http://localhost") or value.startswith("http://127.0.0.1"):
        return value.rstrip("/")
    if not value.startswith("http"):
        return "https://" + value.rstrip("/")
    if value.startswith("http://"):
        return "https://" + value[len("http://") :].rstrip("/")
    return value.rstrip("/")


@main_bp.route("/")
def index():
    all_extractors = PlatformExtractor.query.order_by(PlatformExtractor.display_name).all()
    platform_list = _build_platform_page_items(all_extractors)
    recent_blog_posts = _get_recent_blog_posts(limit=3)
    total_platform_count = _safe_count(
        PlatformExtractor.query.filter_by(is_enabled=True),
        200,
    )
    total_downloads_all_time = _safe_count(
        DownloadJob.query.filter(DownloadJob.status == "complete"),
        128000,
    )
    active_users_estimate = _safe_count(User.query, 12000)
    return render_template(
        "main/index.html",
        platform_list=platform_list,
        recent_blog_posts=recent_blog_posts,
        total_platform_count=total_platform_count,
        total_downloads_all_time=total_downloads_all_time,
        active_users_estimate=active_users_estimate,
    )


@main_bp.route("/features")
def features():
    total_platforms = PlatformExtractor.query.filter_by(is_enabled=True).count()
    return render_template("main/features.html", total_platforms=total_platforms)


@main_bp.route("/platforms")
def platforms():
    all_extractors = PlatformExtractor.query.order_by(PlatformExtractor.display_name).all()
    platform_items = _build_platform_page_items(all_extractors)
    enabled_count = sum(1 for platform in platform_items if platform["status_label"] == "active")
    total_count = len(platform_items)
    return render_template(
        "main/platforms.html",
        platforms=platform_items,
        enabled_count=enabled_count,
        total_count=total_count,
    )


@main_bp.route("/pricing")
def pricing():
    return render_template(
        "main/pricing.html",
        razorpay_key_id=current_app.config.get("RAZORPAY_KEY_ID", ""),
        pro_monthly="₹799",
        pro_annual="₹6,399",
        savings="₹2,189",
    )


@main_bp.route("/blog")
def blog_index():
    page = request.args.get("page", 1, type=int)
    category = request.args.get("category", "")
    search_query = request.args.get("q", "")

    form = BlogSearchForm(formdata=None, data={"query": search_query})

    base_query = BlogPost.query.filter_by(is_published=True)
    if category:
        base_query = base_query.filter(BlogPost.category == category)
    if search_query:
        like_query = f"%{search_query}%"
        base_query = base_query.filter(
            or_(BlogPost.title.ilike(like_query), BlogPost.excerpt.ilike(like_query))
        )

    pagination = base_query.order_by(BlogPost.published_at.desc()).paginate(
        page=page, per_page=9, error_out=False
    )

    categories = (
        db.session.query(BlogPost.category)
        .filter_by(is_published=True)
        .distinct()
        .order_by(BlogPost.category)
        .all()
    )

    popular_posts = (
        BlogPost.query.filter_by(is_published=True)
        .order_by(BlogPost.view_count.desc())
        .limit(5)
        .all()
    )

    category_counts = (
        db.session.query(BlogPost.category, func.count(BlogPost.id))
        .filter_by(is_published=True)
        .group_by(BlogPost.category)
        .order_by(BlogPost.category)
        .all()
    )

    return render_template(
        "main/blog_index.html",
        posts=pagination.items,
        pagination=pagination,
        form=form,
        current_category=category,
        categories=[c[0] for c in categories],
        category_counts={row[0]: row[1] for row in category_counts},
        popular_posts=popular_posts,
        search_query=search_query,
    )


@main_bp.route("/blog/<slug>")
def blog_post(slug):
    post = BlogPost.query.filter_by(slug=slug, is_published=True).first_or_404()
    post.view_count = (post.view_count or 0) + 1
    db.session.commit()

    related = (
        BlogPost.query.filter(
            BlogPost.category == post.category,
            BlogPost.slug != slug,
            BlogPost.is_published.is_(True),
        )
        .order_by(BlogPost.published_at.desc())
        .limit(3)
        .all()
    )

    content_html = post.content or ""
    if content_html and "<" not in content_html:
        content_html = markdown_render(
            content_html,
            extensions=["fenced_code", "tables", "sane_lists"],
        )

    popular_posts = (
        BlogPost.query.filter_by(is_published=True)
        .order_by(BlogPost.view_count.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "main/blog_post.html",
        post=post,
        related_posts=related,
        content_html=content_html,
        popular_posts=popular_posts,
    )


@main_bp.route("/docs")
def docs():
    section = request.args.get("section", "getting-started")
    return render_template("main/docs.html", active_section=section)


@main_bp.route("/changelog")
def changelog():
    return render_template("main/changelog.html")


@main_bp.route("/legal/terms")
def terms():
    return render_template("main/terms.html")


@main_bp.route("/legal/privacy")
def privacy():
    return render_template("main/privacy.html")


@main_bp.route("/contact", methods=["GET", "POST"])
def contact():
    form = ContactForm()
    if form.validate_on_submit():
        support_email = "support@universaldl.com"
        subject = f"[UniversalDL Contact] [{form.subject.data}] from {form.name.data}"
        message_html = (
            "<h2>New contact form submission</h2>"
            f"<p><strong>Name:</strong> {form.name.data}</p>"
            f"<p><strong>Email:</strong> {form.email.data}</p>"
            f"<p><strong>Subject:</strong> {form.subject.data}</p>"
            f"<p><strong>Message:</strong></p><p>{form.message.data}</p>"
        )
        send_email(support_email, subject, message_html)

        AuditLog.log(
            action="contact_form_submit",
            user_id=None,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string,
            detail_json={
                "name": form.name.data,
                "subject": form.subject.data,
                "email_masked": _mask_email(form.email.data),
            },
        )
        flash("Your message has been sent. We'll respond within 48 hours.", "success")
        return redirect(
            url_for("main.contact", sent="1", email=form.email.data)
        )

    return render_template("main/contact.html", form=form)


@main_bp.route("/robots.txt")
def robots_txt():
    robots_content = """User-agent: *
Disallow: /admin/
Disallow: /api/
Disallow: /download/progress/
Disallow: /download/batch/status/
Disallow: /dashboard/
Disallow: /history/
Disallow: /subscriptions/
Disallow: /settings/
Disallow: /onboarding/
Allow: /
Allow: /features
Allow: /platforms
Allow: /pricing
Allow: /blog
Allow: /docs
Allow: /changelog
Allow: /contact

Sitemap: https://universaldl.com/sitemap.xml"""
    return Response(robots_content, mimetype="text/plain")


@main_bp.route("/sw.js")
def service_worker():
    response = current_app.send_static_file("sw.js")
    response.headers["Cache-Control"] = "no-cache"
    return response


@main_bp.route("/offline.html")
def offline_page():
    response = current_app.send_static_file("offline.html")
    response.headers["Cache-Control"] = "no-cache"
    return response


@main_bp.route("/sitemap.xml")
def sitemap_xml():
    base_url = _normalize_base_url(current_app.config.get("APP_DOMAIN"))

    urlset = element_tree.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")

    static_paths = [
        "/",
        "/features",
        "/platforms",
        "/pricing",
        "/blog",
        "/docs",
        "/changelog",
        "/contact",
        "/legal/terms",
        "/legal/privacy",
    ]

    for path in static_paths:
        url_el = element_tree.SubElement(urlset, "url")
        loc_el = element_tree.SubElement(url_el, "loc")
        loc_el.text = base_url + path

    posts = BlogPost.query.filter_by(is_published=True).order_by(BlogPost.published_at.desc()).all()
    for post in posts:
        url_el = element_tree.SubElement(urlset, "url")
        loc_el = element_tree.SubElement(url_el, "loc")
        loc_el.text = base_url + url_for("main.blog_post", slug=post.slug)
        if post.published_at:
            lastmod_el = element_tree.SubElement(url_el, "lastmod")
            lastmod_el.text = post.published_at.date().isoformat()

    xml_output = element_tree.tostring(urlset, encoding="utf-8", xml_declaration=True)
    return Response(xml_output, mimetype="application/xml")


@main_bp.route("/health")
def health_check():
    status = "healthy"
    db_status = "ok"
    redis_status = "ok"

    try:
        db.session.execute(select(1))
    except Exception:
        status = "unhealthy"
        db_status = "error"

    try:
        redis_url = current_app.config.get("REDIS_URL")
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        client.ping()
    except Exception:
        status = "unhealthy"
        redis_status = "error"

    payload = {
        "status": status,
        "db": db_status,
        "redis": redis_status,
        "app_version": current_app.config.get("APP_VERSION"),
        "timestamp": datetime.utcnow().isoformat(),
    }
    http_status = 200 if status == "healthy" else 503
    return jsonify(payload), http_status


@main_bp.route("/set-theme", methods=["POST"])
@csrf.exempt
def set_theme():
    data = request.get_json(silent=True) or {}
    theme = data.get("theme") or request.form.get("theme") or "light"
    if theme not in ("dark", "light"):
        theme = "light"
    session["theme"] = theme
    session.modified = True
    return jsonify({"success": True, "theme": theme})
