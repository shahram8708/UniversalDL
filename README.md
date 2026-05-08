# UniversalDL — Universal Media & Content Downloader

> Download videos, audio, images, and more from 200+ platforms.
> No APIs. No ads. No registration required.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Flask](https://img.shields.io/badge/Flask-Framework-black)
![License](https://img.shields.io/badge/License-MIT-green)
![Docker](https://img.shields.io/badge/Docker-Ready-blue)

## 🌟 Features

- Universal URL detection with 200+ platforms
- Multiple output formats: MP4, MKV, WebM, MP3, FLAC, M4A, WAV
- HLS and DASH adaptive stream support with FFmpeg stitching
- Batch download queue with ZIP export
- Channel subscription monitor with auto-download
- Browser extension for Chrome and Firefox
- Progressive Web App with offline support
- REST API for Pro users
- Subtitle download and embedding
- Metadata injection with chapters and artwork
- Dark and light theme with CSS custom properties
- Admin panel with full system management

## 📋 Requirements

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- FFmpeg installed on the system
- Node.js not required
- Playwright installed via pip

## 🚀 Quick Start (Development)

### 1. Clone the repository

```bash
git clone https://github.com/universaldl/universaldl.git
cd universaldl
```

### 2. Create virtual environment

```bash
python -m venv venv
source venv/bin/activate
```

Windows

```bash
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Set up environment variables

```bash
cp .env.example .env
```

Minimum required values

- SECRET_KEY
- DATABASE_URL
- REDIS_URL

### 5. Set up PostgreSQL database

```bash
createdb universaldl
```

### 6. Run the application

```bash
python run.py
```

The application will

- Automatically create database tables
- Seed the admin user: admin@universaldl.com / Admin@123456
- Seed 15 platform extractors
- Seed 5 sample blog posts
- Create the temp download directory

Visit http://localhost:5000

### 7. Run Celery workers

Worker for download tasks

```bash
celery -A celery_worker.celery worker --loglevel=info -Q downloads,celery
```

Scheduler for periodic tasks

```bash
celery -A celery_worker.celery beat --loglevel=info
```

## 🐳 Docker Deployment

### Development with Docker Compose

```bash
docker-compose up --build
```

This starts Flask web, Celery worker, Celery Beat, PostgreSQL, and Redis.

### Production deployment

1. Update .env with production values
2. Set FLASK_ENV=production in .env
3. Use a strong SECRET_KEY
4. Configure Razorpay live keys
5. Set APP_DOMAIN to your domain

```bash
docker-compose up -d
```

## 👤 Admin Access

Default admin credentials

Email: admin@universaldl.com
Password: Admin@123456

Admin panel: http://localhost:5000/admin

Change the password at: http://localhost:5000/settings?tab=security

## 💳 Razorpay Payment Setup

1. Create an account at razorpay.com
2. Go to Settings → API Keys
3. Generate test keys with prefix rzp_test_
4. Add keys to .env: RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET
5. Webhook setup
   - Webhook URL: https://yourdomain.com/upgrade/webhook
   - Events: payment.captured, payment.failed
   - Add RAZORPAY_WEBHOOK_SECRET to .env

## 📧 Email Setup

### Gmail (Development)

1. Enable 2 factor authentication
2. Create an App Password
3. Use your Gmail address and the app password in .env

### SendGrid (Production)

1. Create SendGrid account and verify sender
2. Create API key with Mail Send permission
3. Set MAIL_USERNAME=apikey and MAIL_PASSWORD=your_sendgrid_api_key
4. Set MAIL_SERVER=smtp.sendgrid.net and MAIL_PORT=587

## 🔐 Google OAuth Setup

1. Go to console.cloud.google.com
2. Create a project
3. APIs and Services → Credentials
4. Create OAuth 2.0 Client ID for Web application
5. Add redirect URIs
   - http://localhost:5000/auth/google/callback
   - https://yourdomain.com/auth/google/callback
6. Add client ID and secret to .env

## 🏗️ Project Structure

```text
universaldl/
├── app/
│   ├── __init__.py         App factory
│   ├── auth/               Authentication routes
│   ├── main/               Public pages
│   ├── downloader/         Download engine and Celery tasks
│   ├── dashboard/          User dashboard and settings
│   ├── admin/              Admin panel
│   ├── api/                REST API
│   ├── models/             SQLAlchemy models
│   ├── services/           Business logic
│   └── extractors/         Platform-specific extractors
├── templates/              Jinja2 templates
├── static/                 CSS, JS, PWA assets
├── migrations/             Database migrations
├── run.py                  Development server
├── wsgi.py                 Production WSGI
├── celery_worker.py        Celery configuration
├── celery_beat.py          Scheduled tasks
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## 🔌 REST API Reference

Base URL: https://yourdomain.com/api/v1
Authentication: Bearer token in Authorization header (Pro plan required)

Endpoints

- POST /analyze
- POST /download
- GET /jobs/<job_id>
- POST /batch
- GET /queue/<batch_id>
- GET /history
- GET /platforms
- GET /formats
- GET /subscriptions

Full docs at /docs

## 🧩 Adding a New Platform Extractor

1. Create app/extractors/yourplatform.py
2. Inherit from BaseExtractor
3. Set PLATFORM_ID, REQUIRES_HEADLESS, TEST_URL
4. Implement extract(url) returning the standard dict
5. Add to EXTRACTOR_REGISTRY in app/services/dispatcher.py
6. Add URL patterns to PLATFORM_PATTERNS in app/services/url_parser.py
7. Seed a PlatformExtractor record via admin panel or seeder
8. Test via admin panel at /admin/extractors

## 📊 Database Models

- User: user accounts, plan status, API keys, preferences
- DownloadJob: individual download tasks with progress tracking
- BatchQueue: groups of URLs submitted for batch download
- Subscription: channel monitor subscriptions for auto-download
- PlatformExtractor: extractor configuration and health status
- AuditLog: security and compliance event logging
- APIUsage: Pro API call tracking for rate limiting
- BlogPost: blog content management

## ⚙️ Environment Variables

Refer to .env.example for full documentation. Key variables include

- SECRET_KEY required
- DATABASE_URL required
- REDIS_URL required
- RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET for payments
- GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET for OAuth
- MAIL_SERVER settings for email delivery
- APP_DOMAIN for absolute URLs and sitemap

## 🔒 Security

- bcrypt password hashing with 12 rounds
- CSRF protection on all forms
- Secure cookies with SameSite
- API key SHA-256 hashing
- Rate limiting per IP and API key
- No persistent media storage
- HTTPS enforcement with HSTS in production
- Content Security Policy headers
- SQL injection prevention via ORM only
- DRM detection and blocking
- Audit logging for sensitive actions

## 📝 License

MIT License. See LICENSE for details.

## 🤝 Contributing

- Fork the repository
- Create a feature branch
- Run tests with pytest tests/
- Submit a pull request
- Follow the extractor guide for new platforms

## 📞 Support

- Documentation: /docs
- Bug reports: /contact (select Bug Report)
- Email: support@universaldl.com
- Community: Discord (link placeholder)
- Pro support: email with 48 hour response SLA
