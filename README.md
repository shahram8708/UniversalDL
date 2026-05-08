# UniversalDL

> **Download videos, audio, and media from 200+ platforms — YouTube, TikTok, Instagram, Twitter, Reddit, Twitch, Vimeo, SoundCloud, Bilibili, Spotify, LinkedIn, Pinterest, and many more. One link is all it takes.**

[![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-Latest-lightgrey?logo=flask)](https://flask.palletsprojects.com/)
[![Celery](https://img.shields.io/badge/Celery-Async%20Tasks-brightgreen?logo=celery)](https://docs.celeryq.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-blue?logo=postgresql)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-red?logo=redis)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-Not%20Specified-yellow)]()
[![Version](https://img.shields.io/badge/Version-1.0.0-informational)]()
[![PWA](https://img.shields.io/badge/PWA-Ready-5A0FC8?logo=pwa)]()
[![Chrome Extension](https://img.shields.io/badge/Chrome%20Extension-MV3-4285F4?logo=googlechrome)](https://developer.chrome.com/docs/extensions/)

---

## Table of Contents

1. [About the Project](#about-the-project)
2. [Key Features](#key-features)
3. [Tech Stack](#tech-stack)
4. [Project Structure](#project-structure)
5. [Getting Started](#getting-started)
   - [Prerequisites](#prerequisites)
   - [Installation](#installation)
   - [Environment Variables](#environment-variables)
   - [Running the Project](#running-the-project)
6. [Usage](#usage)
7. [API Documentation](#api-documentation)
8. [Configuration](#configuration)
9. [Testing](#testing)
10. [Deployment](#deployment)
11. [Contributing](#contributing)
12. [Roadmap](#roadmap)
13. [License](#license)
14. [Acknowledgements](#acknowledgements)
15. [Contact / Author](#contact--author)

---

## About the Project

There are dozens of media downloaders out there. Most are clunky browser tools, shady executable files, or APIs that break every time a platform updates its layout. UniversalDL was built to be the serious alternative — a fully self-hosted, production-grade platform that handles the hard parts for you.

At its core it is a web application powered by Flask, Celery, and a modular extractor system with purpose-built scrapers for 20+ major platforms. Every download runs asynchronously in a background worker, streams progress back to the browser in real time, and stores a signed download URL that expires automatically after an hour. The whole thing runs in Docker with four service containers: the web app, a Celery worker, a Celery beat scheduler, and a Redis + PostgreSQL data layer.

It is built for developers who want a self-hosted download infrastructure, small teams who want a private media archival tool, and anyone who is tired of paste-a-link-and-pray services that go offline without notice. A companion Chrome extension (Manifest V3) integrates directly with the web app so you can trigger downloads from any supported page with a single click or keyboard shortcut.

---

## Key Features

- **20+ Purpose-Built Extractors** — dedicated extractors for YouTube, TikTok, Instagram, Twitter/X, Reddit, Twitch, Vimeo, SoundCloud, Bilibili, Facebook, Dailymotion, Spotify, LinkedIn, Pinterest, Podcast RSS feeds, Coursera, Behance, Imgur, Netflix (metadata), and a generic fallback for everything else.
- **Async Download Queue** — every job is dispatched to Celery workers so the web server never blocks; download progress, speed, and ETA are streamed back live.
- **Multiple Output Formats** — export as MP4, MKV, WebM, MP3, FLAC, M4A, or WAV; quality selection from best down to 480p or audio-only.
- **Batch Downloads** — submit up to N URLs at once and receive a single ZIP archive when all jobs complete.
- **Channel Subscriptions (Pro)** — subscribe to YouTube channels, Twitch streams, SoundCloud profiles, Spotify podcasts, Reddit subreddits, or Bilibili channels and have new content downloaded automatically on hourly, daily, or weekly schedules.
- **REST API with API Key Auth** — a full programmatic API protected by Bearer tokens; every request is rate-limited and logged with response-time telemetry.
- **Chrome Extension (MV3)** — inject a download button into supported pages, trigger quick downloads via `Ctrl+Shift+2`, and manage API settings from the extension options page.
- **Progressive Web App** — installable on any device; includes offline fallback page, share target support, and home screen shortcuts.
- **Google OAuth Login** — sign in with Google in addition to email/password; accounts are linked automatically when the same email is detected.
- **Razorpay Payments** — built-in subscription upgrade flow with webhook signature verification for Pro and Enterprise plan billing.
- **Admin Panel** — dedicated admin dashboard to manage users, monitor the download queue, toggle extractor health, view audit logs, and update platform settings.
- **Signed, Expiring Download URLs** — completed files are served via cryptographically signed tokens that expire after a configurable period (default 1 hour), then purged from disk automatically.
- **Proxy Pool Support** — optional proxy provider integration to bypass per-platform rate limiting and geo-restrictions.
- **Sentry Error Monitoring** — first-class Sentry SDK integration for capturing production exceptions.
- **Audit Logging** — every significant user action (login, download, plan change, API key rotation) is recorded with IP, user agent, and resource ID.

---

## Tech Stack

### Backend
| Technology | Purpose |
|---|---|
| Python 3.11 | Runtime language |
| Flask | Web framework |
| Flask-SQLAlchemy | ORM |
| Flask-Migrate | Database migrations |
| Flask-Login | Session-based authentication |
| Flask-Bcrypt | Password hashing |
| Flask-WTF | CSRF-protected forms |
| Flask-Mail | Transactional email (SMTP) |
| Flask-CORS | Cross-origin resource sharing |
| Flask-Limiter | Rate limiting via Redis |
| Authlib | Google OAuth 2.0 / OpenID Connect |
| Celery | Async task queue |
| yt-dlp | Core media extraction engine |
| Playwright | Headless Chromium for JavaScript-heavy platforms |
| ffmpeg-python | Video and audio transcoding |
| m3u8 | HLS stream parsing |
| mutagen | Audio metadata tagging |
| Pillow | Image processing |
| httpx | Async HTTP client |
| Pydantic | API request/response validation |
| itsdangerous | Signed URL tokens |
| Razorpay | Payment gateway SDK |
| Sentry SDK | Error monitoring |
| Gunicorn | Production WSGI server |

### Frontend
| Technology | Purpose |
|---|---|
| Jinja2 Templates | Server-rendered HTML |
| Custom CSS (3 files) | Main, components, and admin stylesheets |
| Vanilla JavaScript (12 modules) | Download flow, batch, dashboard, settings, subscriptions, progress, PWA, onboarding |
| Service Worker (`sw.js`) | PWA offline support and caching |

### Database and Infrastructure
| Technology | Purpose |
|---|---|
| PostgreSQL 15 | Primary relational database |
| Redis 7 | Celery broker, result backend, rate-limit store, media-info cache |
| Docker + Docker Compose | Container orchestration (web, worker, beat, db, redis) |
| FFmpeg | Installed in Docker runtime image for media conversion |
| Chromium | Installed in Docker runtime image for Playwright |

### Chrome Extension
| Technology | Purpose |
|---|---|
| Manifest V3 | Extension platform |
| Service Worker (`background/service_worker.js`) | Background task coordination |
| Content Script (`content/content_script.js`) | Page injection for supported platforms |
| Options Page | Extension configuration and API key management |

---

## Project Structure

```
UniversalDL-main/
├── app/                        # Main Flask application package
│   ├── __init__.py             # App factory (create_app), blueprint registration
│   ├── config.py               # Config classes: Base, Development, Production
│   ├── extensions.py           # Shared Flask extensions (db, bcrypt, login_manager, limiter)
│   ├── celery_app.py           # Celery instance and init_celery helper
│   │
│   ├── admin/                  # Admin panel blueprint
│   │   ├── __init__.py
│   │   ├── forms.py            # Admin forms (settings, extractor toggle)
│   │   └── routes.py           # Admin dashboard, users, queue, logs, extractor mgmt
│   │
│   ├── api/                    # REST API blueprint (v1)
│   │   ├── __init__.py
│   │   ├── auth.py             # API key authentication and usage logging
│   │   ├── routes.py           # /analyze, /download, /jobs, /batch, /queue, /history, /subscriptions, /formats, /platforms
│   │   └── serializers.py      # Pydantic request/response schemas
│   │
│   ├── auth/                   # Authentication blueprint
│   │   ├── __init__.py
│   │   ├── forms.py            # Login, register, reset password forms
│   │   ├── oauth.py            # Google OAuth 2.0 login flow
│   │   └── routes.py           # /login, /register, /logout, /reset-password
│   │
│   ├── dashboard/              # User dashboard blueprint
│   │   ├── __init__.py
│   │   ├── forms.py            # Settings, subscription, API key forms
│   │   └── routes.py           # /dashboard, /history, /settings, /subscriptions, /upgrade
│   │
│   ├── downloader/             # Download blueprint
│   │   ├── __init__.py
│   │   ├── forms.py            # URL input and quality selector forms
│   │   ├── routes.py           # /download, /download/batch, /download/file/<token>
│   │   └── tasks.py            # Celery tasks: analyze_url_task, download_media_task, cleanup, subscription poll
│   │
│   ├── extractors/             # Platform-specific media extractors
│   │   ├── __init__.py         # Exports all extractor classes
│   │   ├── base.py             # BaseExtractor ABC and ExtractorError
│   │   ├── youtube.py          # YouTube extractor (yt-dlp backed)
│   │   ├── tiktok.py           # TikTok extractor (Playwright/headless)
│   │   ├── instagram.py        # Instagram extractor
│   │   ├── twitter.py          # Twitter/X extractor
│   │   ├── reddit.py           # Reddit extractor
│   │   ├── twitch.py           # Twitch VOD/clip extractor
│   │   ├── vimeo.py            # Vimeo extractor
│   │   ├── soundcloud.py       # SoundCloud extractor
│   │   ├── bilibili.py         # Bilibili extractor
│   │   ├── facebook.py         # Facebook video extractor
│   │   ├── dailymotion.py      # Dailymotion extractor
│   │   ├── spotify.py          # Spotify (metadata + audio routing)
│   │   ├── linkedin.py         # LinkedIn video extractor (Playwright)
│   │   ├── pinterest.py        # Pinterest image/video extractor
│   │   ├── podcast_rss.py      # Generic RSS/podcast feed extractor
│   │   ├── netflix.py          # Netflix (metadata only, DRM note)
│   │   ├── coursera.py         # Coursera lecture extractor
│   │   ├── behance.py          # Behance project media extractor
│   │   ├── imgur.py            # Imgur image/album extractor
│   │   └── generic.py          # Generic fallback extractor
│   │
│   ├── main/                   # Public pages blueprint
│   │   ├── __init__.py
│   │   ├── forms.py            # Contact form
│   │   └── routes.py           # /, /features, /platforms, /pricing, /docs, /blog, /contact, /changelog
│   │
│   ├── models/                 # SQLAlchemy database models
│   │   ├── __init__.py
│   │   ├── user.py             # User model (plan, API key, OAuth, settings)
│   │   ├── download_job.py     # DownloadJob model (status, progress, signed URL)
│   │   ├── batch_queue.py      # BatchQueue model (multi-URL jobs, ZIP URL)
│   │   ├── subscription.py     # Subscription model (channel, frequency, known IDs)
│   │   ├── extractor.py        # PlatformExtractor model (health tracking, success rates)
│   │   ├── audit_log.py        # AuditLog model (action, IP, user agent, resource)
│   │   ├── api_usage.py        # APIUsage model (endpoint, method, status, response_ms)
│   │   └── blog_post.py        # BlogPost model (title, slug, content, published)
│   │
│   └── services/               # Business logic and external integrations
│       ├── __init__.py
│       ├── dispatcher.py       # Extractor registry and dispatch_and_extract()
│       ├── ffmpeg.py           # FFmpeg wrapper for transcoding and merging
│       ├── metadata.py         # Media metadata normalisation
│       ├── notify.py           # Email notifications (download complete, failed, subscription)
│       ├── proxy.py            # Proxy pool management
│       ├── razorpay_service.py # Razorpay order creation and webhook verification
│       ├── storage.py          # File storage, signed URL generation, ZIP archive creation
│       └── url_parser.py       # URL normalisation and platform detection
│
├── chrome-extension/           # Chrome Extension (Manifest V3)
│   ├── manifest.json           # Extension manifest, permissions, content script matches
│   ├── background/
│   │   └── service_worker.js   # Background service worker (job polling, notifications)
│   ├── content/
│   │   └── content_script.js   # Injected button on supported pages
│   ├── popup/
│   │   ├── popup.html          # Extension popup UI
│   │   ├── popup.css           # Popup styles
│   │   └── popup.js            # Popup logic (download trigger, progress display)
│   ├── options/
│   │   ├── options.html        # Options page
│   │   ├── options.css         # Options styles
│   │   └── options.js          # Options page logic (API key, server URL)
│   └── icons/                  # SVG icons at 16, 32, 48, 128px
│
├── docker/
│   └── init.sql                # PostgreSQL initialisation (uuid-ossp extension)
│
├── static/
│   ├── css/
│   │   ├── main.css            # Global styles
│   │   ├── components.css      # Reusable component styles
│   │   └── admin.css           # Admin panel styles
│   ├── js/
│   │   ├── main.js             # Core UI helpers and platform detection
│   │   ├── download.js         # Download page logic and progress polling
│   │   ├── batch.js            # Batch download page logic
│   │   ├── dashboard.js        # Dashboard widgets and stats
│   │   ├── history.js          # Download history table and filtering
│   │   ├── settings.js         # User settings page
│   │   ├── subscriptions.js    # Subscription management
│   │   ├── upgrade.js          # Razorpay checkout integration
│   │   ├── api_settings.js     # API key generation and display
│   │   ├── onboarding.js       # New user onboarding flow
│   │   ├── progress.js         # Progress bar component
│   │   ├── admin.js            # Admin panel interactions
│   │   └── pwa.js              # PWA installation prompt handling
│   ├── manifest.json           # PWA web app manifest
│   ├── sw.js                   # Service worker for PWA caching
│   └── offline.html            # PWA offline fallback page
│
├── templates/                  # Jinja2 HTML templates
│   ├── base.html               # Base layout with nav and flash messages
│   ├── admin/                  # Admin panel templates
│   ├── auth/                   # Login, register, reset password
│   ├── components/             # Reusable partials (navbar, footer, cards, pagination)
│   ├── dashboard/              # User dashboard pages
│   ├── downloader/             # Download and batch pages
│   ├── errors/                 # 403, 404, 500 error pages
│   └── main/                   # Public landing, features, pricing, docs, blog, contact
│
├── Dockerfile                  # Multi-stage build (builder + runtime with FFmpeg + Chromium)
├── docker-compose.yml          # Five-service Compose stack
├── run.py                      # Development server entry point
├── wsgi.py                     # Gunicorn WSGI entry point
├── celery_worker.py            # Celery worker entry point
├── celery_beat.py              # Celery beat schedule definitions
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── .gitignore                  # Git ignore rules
├── .dockerignore               # Docker build ignore rules
└── README.md                   # This file
```

---

## Getting Started

### Prerequisites

Make sure you have the following installed before you begin.

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| Docker | 24.x+ | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | v2+ | Included with Docker Desktop |
| Git | Any | [git-scm.com](https://git-scm.com/) |
| FFmpeg | 6.x+ | [ffmpeg.org](https://ffmpeg.org/download.html) (local dev only; included in Docker) |

If you plan to run locally without Docker you will also need:
- PostgreSQL 15+
- Redis 7+
- Chromium or Chrome (for Playwright headless browsing)

---

### Installation

**Option A — Docker (recommended)**

1. Clone the repository.

```bash
git clone https://github.com/shahram8708/UniversalDL.git
cd UniversalDL
```

2. Copy the environment template and fill in your values.

```bash
cp .env.example .env
```

3. Build and start all five services.

```bash
docker compose up --build
```

4. On first run, create the database tables.

```bash
docker compose exec web flask db upgrade
```

The app will be available at `http://localhost:5000`.

**Option B — Local Development (without Docker)**

1. Clone and enter the directory.

```bash
git clone https://github.com/shahram8708/UniversalDL.git
cd UniversalDL
```

2. Create and activate a virtual environment.

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
```

3. Install Python dependencies.

```bash
pip install -r requirements.txt
```

4. Install Playwright browsers.

```bash
playwright install chromium
```

5. Copy and configure the environment file.

```bash
cp .env.example .env
# Edit .env and set DATABASE_URL, REDIS_URL, and other required values
```

6. Run database migrations.

```bash
flask db upgrade
```

7. Start Redis and PostgreSQL externally (or via Docker separately).

```bash
docker run -d -p 5432:5432 -e POSTGRES_DB=universaldl -e POSTGRES_USER=universaldl -e POSTGRES_PASSWORD=changeme postgres:15-alpine
docker run -d -p 6379:6379 redis:7-alpine
```

8. Start the Flask dev server.

```bash
python run.py
```

9. In a second terminal, start the Celery worker.

```bash
celery -A celery_worker.celery worker --loglevel=info --concurrency=4 -Q downloads,celery
```

10. In a third terminal, start the Celery beat scheduler.

```bash
celery -A celery_worker.celery beat --loglevel=info --schedule=/tmp/celerybeat-schedule
```

---

### Environment Variables

Copy `.env.example` to `.env` and fill in every value. Variables marked **required** will cause startup warnings if missing.

| Variable | Description | Example |
|---|---|---|
| `SECRET_KEY` | Flask secret key for sessions and signed URLs. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` | `abc123...` |
| `FLASK_ENV` | Runtime environment | `development` or `production` |
| `PORT` | Port for the dev server | `5000` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://universaldl:password@localhost:5432/universaldl` |
| `DB_PASSWORD` | Postgres password used by Docker Compose | `changeme_in_production` |
| `REDIS_URL` | Redis connection string (broker, cache, rate limiter) | `redis://localhost:6379/0` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | `1234.apps.googleusercontent.com` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | `GOCSPX-abc123` |
| `MAIL_SERVER` | SMTP host | `smtp.gmail.com` |
| `MAIL_PORT` | SMTP port | `587` |
| `MAIL_USE_TLS` | Enable TLS | `true` |
| `MAIL_USERNAME` | SMTP login username | `your@gmail.com` |
| `MAIL_PASSWORD` | SMTP password or app password | `your-app-password` |
| `MAIL_DEFAULT_SENDER` | From address for emails | `noreply@universaldl.com` |
| `RAZORPAY_KEY_ID` | Razorpay API key ID | `rzp_test_xxxxxxxxxxxx` |
| `RAZORPAY_KEY_SECRET` | Razorpay API key secret | `your-secret` |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook verification secret | `your-webhook-secret` |
| `TEMP_DOWNLOAD_DIR` | Directory for temporary download files | `/tmp/universaldl` |
| `MAX_FILE_AGE_SECONDS` | How long completed files are kept before purge | `3600` |
| `SENTRY_DSN` | Sentry error tracking DSN (leave empty to disable) | `https://...@sentry.io/123` |
| `PROXY_PROVIDER_URL` | Optional proxy pool API endpoint | `https://proxy.example.com/api` |
| `PROXY_PROVIDER_KEY` | Optional proxy pool API key | `your-proxy-key` |
| `PAGERDUTY_INTEGRATION_KEY` | Optional PagerDuty alert key | `your-pd-key` |
| `APP_DOMAIN` | Base URL for absolute links in emails and sitemap | `https://universaldl.onrender.com` |

---

### Running the Project

**Development mode**

```bash
python run.py
```

This starts Flask's built-in dev server with debug enabled (auto-reloader disabled to play nicely with Celery). Access at `http://localhost:5000`.

**Production mode via Gunicorn**

```bash
gunicorn \
  --workers 4 \
  --threads 2 \
  --worker-class gthread \
  --bind 0.0.0.0:5000 \
  --timeout 120 \
  --keepalive 5 \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --access-logfile - \
  --error-logfile - \
  wsgi:application
```

**Celery worker**

```bash
celery -A celery_worker.celery worker --loglevel=info --concurrency=4 -Q downloads,celery --max-tasks-per-child=50
```

**Celery beat scheduler**

```bash
celery -A celery_worker.celery beat --loglevel=info --schedule=/tmp/celerybeat-schedule
```

**Docker Compose (all services together)**

```bash
docker compose up            # foreground
docker compose up -d         # detached
docker compose logs -f web   # follow web logs
docker compose down          # stop and remove containers
```

---

## Usage

### Web Interface

1. Open `http://localhost:5000` in your browser.
2. Paste any supported URL into the input field on the homepage or `/download` page.
3. Select your desired quality (Best, 1080p, 720p, 480p, Audio Only) and output format (MP4, MKV, WebM, MP3, FLAC, M4A, WAV).
4. Click **Download**. The job is queued immediately and you see a live progress bar showing percentage, download speed, and estimated time remaining.
5. When complete, a signed download link appears. It expires after 1 hour. Grab your file before then.

### Batch Downloads

Navigate to `/download/batch`, paste up to the plan limit of URLs (one per line), choose a quality and format, and submit. All jobs run in parallel. When all complete you receive a single downloadable ZIP archive.

### Chrome Extension

1. Open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select the `chrome-extension/` folder.
2. Click the UniversalDL icon in your toolbar while on any supported page.
3. Use `Ctrl+Shift+1` (or `Cmd+Shift+1` on Mac) to open the popup, or `Ctrl+Shift+2` to trigger a quick download of the current page URL.
4. Open the extension options page to set your API key and server URL.

---

## API Documentation

All endpoints are under `/api/v1`. Authentication requires a `Bearer` token in the `Authorization` header or an `X-API-Key` header. API keys are available to Pro and Enterprise plan users from the dashboard settings page.

```
Authorization: Bearer YOUR_API_KEY
```

### POST `/api/v1/analyze`

Analyze a URL and immediately queue a download job.

**Rate limit:** 60 per minute

**Request body:**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

**Response `202`:**
```json
{
  "success": true,
  "status": "analyzing",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Analysis started. Poll /api/v1/jobs/{job_id} for status."
}
```

---

### POST `/api/v1/download`

Queue a download with explicit quality and format preferences.

**Rate limit:** 200 per hour

**Request body:**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "quality": "1080p",
  "format": "mp4",
  "subtitle_language": "en",
  "subtitle_embed": false
}
```

**Response `202`:**
```json
{
  "success": true,
  "status": "queued",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Download queued. Poll /api/v1/jobs/{job_id} for status."
}
```

---

### GET `/api/v1/jobs/<job_id>`

Poll the status of a download job.

**Rate limit:** 300 per minute

**Response `200`:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "complete",
  "platform": "youtube",
  "title": "Rick Astley - Never Gonna Give You Up",
  "content_type": "video",
  "selected_quality": "1080p",
  "selected_format": "mp4",
  "progress_pct": 100,
  "file_size_bytes": 52428800,
  "download_url": "/download/file/<signed-token>",
  "created_at": "2026-05-08T10:00:00Z",
  "completed_at": "2026-05-08T10:00:45Z"
}
```

Possible `status` values: `queued`, `analyzing`, `pending_download`, `downloading`, `converting`, `complete`, `failed`, `cancelled`.

---

### POST `/api/v1/batch`

Submit multiple URLs as a single batch job.

**Rate limit:** 20 per hour

**Request body:**
```json
{
  "urls": [
    "https://www.youtube.com/watch?v=abc",
    "https://www.tiktok.com/@user/video/123"
  ],
  "quality": "720p",
  "format": "mp4"
}
```

**Response `202`:**
```json
{
  "success": true,
  "batch_id": "660e9400-...",
  "total_jobs": 2,
  "status": "queued",
  "message": "Batch queued. Poll /api/v1/queue/{batch_id} for status."
}
```

---

### GET `/api/v1/queue/<batch_id>`

Get the status of all jobs in a batch.

**Rate limit:** 300 per minute

**Response `200`:**
```json
{
  "batch_id": "660e9400-...",
  "status": "complete",
  "total_jobs": 2,
  "completed_jobs": 2,
  "failed_jobs": 0,
  "overall_pct": 100,
  "zip_url": "/download/file/<signed-token>",
  "jobs": [ ... ]
}
```

---

### GET `/api/v1/history`

Retrieve paginated download history.

**Rate limit:** 300 per minute

**Query parameters:** `page`, `per_page` (max 100), `platform`, `status`

---

### GET `/api/v1/subscriptions`

List all channel subscriptions. **Pro plan required.**

**Rate limit:** 300 per minute

---

### POST `/api/v1/subscriptions`

Create a new channel subscription. Supported platforms: `youtube`, `twitch`, `soundcloud`, `spotify`, `reddit`, `bilibili`.

**Rate limit:** 60 per hour

**Request body:**
```json
{
  "channel_url": "https://www.youtube.com/@channelname",
  "quality": "best",
  "format": "mp4",
  "frequency": "daily",
  "notification_email": true
}
```

---

### DELETE `/api/v1/subscriptions/<sub_id>`

Delete a channel subscription.

**Rate limit:** 60 per hour

---

### GET `/api/v1/formats`

List all supported output formats with codec details.

**Rate limit:** 300 per minute

---

### GET `/api/v1/platforms`

List all registered platform extractors with their current health status, 7-day success rate, and headless-browser requirement.

**Rate limit:** 300 per minute

---

## Configuration

### `app/config.py`

Three config classes exist: `DevelopmentConfig`, `ProductionConfig`, and `BaseConfig` (used for testing).

In development, the database defaults to a local SQLite file (`universaldl_dev.db`) so you can run without PostgreSQL. In production, `DATABASE_URL` is required and PostgreSQL is used.

Key defaults you may want to adjust:

| Setting | Default | Description |
|---|---|---|
| `MAX_FILE_AGE_SECONDS` | `3600` | Files older than this are purged by the Celery beat task |
| `PRO_PRICE_MONTHLY_PAISE` | `79900` | Monthly Pro price in INR paise (₹799) |
| `PRO_PRICE_ANNUAL_PAISE` | `639900` | Annual Pro price in INR paise (₹6,399) |
| `WTF_CSRF_TIME_LIMIT` | `3600` | CSRF token validity in seconds |
| `SQLALCHEMY_ENGINE_OPTIONS` | pool_recycle 300, pre_ping | SQLAlchemy connection pool settings |

### Celery Beat Schedule (`celery_beat.py`)

| Task | Schedule | Purpose |
|---|---|---|
| `tasks.cleanup_files` | Every 15 minutes | Purge expired temporary download files from disk |
| `tasks.subscription_poll` | Every 30 minutes | Check subscribed channels for new content |
| `tasks.extractor_health_check` | Every 30 minutes | Test each platform extractor and update health status |
| `tasks.calculate_success_rates` | Every hour | Recalculate 7-day success rate per platform |
| `tasks.purge_old_url_logs` | Daily at 03:00 | Delete stale URL audit log entries |

### Chrome Extension (`chrome-extension/options/`)

Open the extension options page to configure:
- **Server URL** — where your UniversalDL instance is running (default `http://localhost:5000`).
- **API Key** — your Pro account API key for triggering downloads from the extension.
- **Default quality and format** preferences.

---

## Testing

There is currently no automated test suite in the repository. The project does not include any `tests/` directory, pytest configuration, or test files.

If you want to contribute tests, the recommended setup would be:

```bash
pip install pytest pytest-flask
```

Write tests against the `BaseConfig` (which uses a SQLite in-memory database) and use Flask's `test_client()`. Any contributions that include test coverage are very welcome.

---

## Deployment

### Docker Compose (Self-Hosted)

This is the recommended production deployment path. The `docker-compose.yml` defines five services: `db` (Postgres 15), `redis` (Redis 7), `web` (Gunicorn), `celery_worker`, and `celery_beat`.

1. Copy and configure your `.env` file on the server.

```bash
cp .env.example .env
# Set FLASK_ENV=production, strong SECRET_KEY, real DATABASE_URL, real REDIS_URL
```

2. Build and start all services.

```bash
docker compose up --build -d
```

3. Run database migrations.

```bash
docker compose exec web flask db upgrade
```

4. Check service health.

```bash
docker compose ps
docker compose logs -f
```

The web container exposes port `5000`. Put an Nginx or Caddy reverse proxy in front of it for TLS termination and serving static files efficiently.

**Sample Nginx config snippet:**

```nginx
server {
    listen 443 ssl;
    server_name universaldl.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }

    location /static/ {
        alias /path/to/UniversalDL/static/;
        expires 7d;
    }
}
```

### Dockerfile Details

The Dockerfile uses a two-stage build:
- **`builder`** stage: installs Python packages to `/root/.local` using a `gcc` + `libpq-dev` build environment.
- **`runtime`** stage: installs FFmpeg, Chromium, and all Playwright browser dependencies; copies built packages from the builder; creates a non-root `appuser`; and runs Gunicorn with 4 workers and 2 threads per worker.

The health check polls `http://localhost:5000/health` every 30 seconds with a 60-second start period.

### Environment-Specific Notes

- Set `SESSION_COOKIE_SECURE=True` (enforced automatically in `ProductionConfig`) — requires HTTPS.
- Set `SENTRY_DSN` to enable real-time error reporting.
- Set `PROXY_PROVIDER_URL` and `PROXY_PROVIDER_KEY` if you need to bypass platform geo-restrictions.
- The `TEMP_DOWNLOAD_DIR` volume (`temp_downloads`) is shared between the `web`, `celery_worker` containers so that the web server can serve files that the worker downloaded.

---

## Contributing

Contributions are welcome and appreciated. Here is how to get involved.

**Steps to contribute:**

1. Fork the repository on GitHub.

2. Create a feature branch from `main`.

```bash
git checkout -b feature/your-feature-name
```

3. Make your changes. Keep commits focused and write meaningful commit messages.

4. Push your branch and open a Pull Request against `main`.

```bash
git push origin feature/your-feature-name
```

5. Fill in the PR description: what changed, why, and how to test it.

**Adding a new platform extractor:**

The cleanest way to extend the platform list is to follow the pattern in `app/extractors/`. Create a new file like `app/extractors/myplatform.py`, subclass `BaseExtractor` from `base.py`, implement the `extract(url) -> dict` method (return a dict with at minimum `title` and `qualities` keys), then register your class in both `app/extractors/__init__.py` and `app/services/dispatcher.py`'s `EXTRACTOR_REGISTRY`.

**Bug reports:**

When opening an issue, please include:
- The URL you tried to download (or a redacted version)
- The platform it belongs to
- The error message or unexpected behavior you saw
- Your Python version and whether you are running Docker or local

**Feature requests:**

Open an issue describing the feature, why you want it, and any implementation ideas you have. Tag it with the `enhancement` label.

**Code style:**

Follow PEP 8 for Python code. Use `f-strings` for string formatting. Add `logger.info/warning/error` calls where appropriate — the existing code relies heavily on structured logging to diagnose extractor failures.

---

## Roadmap

Based on the current codebase, here are features that are either partially implemented or natural next steps.

| Status | Item |
|---|---|
| Done | 20+ platform extractors |
| Done | Celery async download queue with real-time progress |
| Done | Batch downloads with ZIP archive delivery |
| Done | Channel subscriptions with configurable polling frequency |
| Done | REST API with Bearer token auth and rate limiting |
| Done | Chrome Extension (MV3) with content script injection |
| Done | PWA with offline support and share target |
| Done | Razorpay payment integration (INR) |
| Done | Google OAuth login |
| Done | Admin panel with extractor health dashboard |
| Done | Sentry error monitoring integration |
| Planned | Add automated test suite (pytest + pytest-flask) |
| Planned | Stripe payment support alongside Razorpay (for non-INR markets) |
| Planned | S3 / object storage backend as an alternative to local temp files |
| Planned | Firefox extension (port from Chrome MV3) |
| Planned | Subtitle download and embedding support across all video extractors |
| Planned | WebSocket-based progress updates instead of polling |
| Planned | Per-user download quota enforcement in the UI (currently backend only) |
| Planned | Dark/light mode toggle in user settings |

---

## License

No `LICENSE` file was found in the repository at the time of this analysis. The project does not currently declare a license.

If you are the author, consider adding an open-source license (MIT, Apache 2.0, or AGPL-3.0 are common choices for web tools like this) so that others know what they are allowed to do with the code.

---

## Acknowledgements

UniversalDL is built on the shoulders of some excellent open-source projects.

- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — the backbone of the media extraction engine; without it a significant portion of the platform support would not exist.
- **[Flask](https://flask.palletsprojects.com/)** — the lightweight web framework that keeps the backend clean and extensible.
- **[Celery](https://docs.celeryq.dev/)** — reliable distributed task queue that makes async downloads possible without a custom job runner.
- **[Playwright](https://playwright.dev/python/)** — headless Chromium automation for platforms like TikTok, LinkedIn, and Coursera that require JavaScript rendering.
- **[FFmpeg](https://ffmpeg.org/)** — the undisputed standard for audio and video transcoding.
- **[Razorpay](https://razorpay.com/)** — payment processing SDK with clean webhook support for subscription management.
- **[Sentry](https://sentry.io/)** — error tracking that makes production debugging far less painful.
- **[Authlib](https://authlib.org/)** — the cleanest Python implementation of OAuth 2.0 / OpenID Connect for Flask.
- **[Pydantic](https://docs.pydantic.dev/)** — API request validation with excellent error messages.
- **[itsdangerous](https://itsdangerous.palletsprojects.com/)** — cryptographic signing for secure, expiring download URLs.

---

## Contact / Author

Author information was not found in the repository (no `package.json`, git config references, or explicit author fields in the code). If you are the maintainer, feel free to add your details here.

If you have questions, found a bug, or just want to say hello — open an issue on GitHub. The project is actively structured for community contributions and the codebase is clean enough to navigate without a tour guide.

Happy downloading.