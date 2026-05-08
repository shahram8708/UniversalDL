# UniversalDL Chrome Extension

## What it does
Downloads videos, audio, images, and media from 200+ platforms directly from your browser. Powered by your UniversalDL server.

## Requirements
1. Google Chrome 114 or newer
2. UniversalDL server running locally or on your own server

## Installation (Development Mode)

### Step 1: Set up UniversalDL server
If not already done:

```bash
git clone [repo] && cd universaldl
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
python run.py
```

### Step 2: Load the extension in Chrome
1. Open Chrome and go to `chrome://extensions`
2. Enable Developer mode toggle
3. Click Load unpacked
4. Select the `chrome-extension/` folder
5. UniversalDL icon appears in your toolbar

### Step 3: Configure the extension
1. Click extension icon
2. If server is running, it connects automatically
3. For Pro features, click settings icon and enter API key
4. Get API key from UniversalDL website Settings API Keys

## How to use

### Method 1: From any supported page
1. Open YouTube, TikTok, Instagram, Reddit, and others
2. Click UniversalDL extension icon
3. Current page URL is auto detected
4. Select quality and format
5. Click Download

### Method 2: Floating button on page
The extension injects a red Download button on supported pages.
Click it and popup opens with the current media URL.

### Method 3: Custom URL
1. Click extension icon
2. Paste URL into Or paste any other URL field
3. Click Analyze
4. Select quality and format
5. Click Download

### Method 4: Keyboard shortcuts
1. Ctrl+Shift+1 opens extension popup
2. Ctrl+Shift+2 starts quick download analyze on current tab
3. If Chrome says the shortcut is unavailable, set it manually from `chrome://extensions/shortcuts`

### Method 5: Right click context menu
1. Right click any video, audio, image, or link
2. Choose Download with UniversalDL

## Supported Platforms
Full list of 200+ supported platforms matches the main UniversalDL app.

## Chrome Web Store Submission
1. Convert SVG icons to PNG using [icons/README.md](icons/README.md)
2. Zip the `chrome-extension/` folder
3. Open Chrome Web Store Developer Dashboard
4. Upload zip package
5. Fill listing metadata and screenshots
6. Submit for review, typically 1 to 3 business days

## Troubleshooting

Server not running:
1. Ensure `python run.py` is running in UniversalDL root folder
2. Confirm port 5000 is not blocked by firewall

CORS error:
1. UniversalDL config includes extension CORS support
2. If error appears, verify Flask app restarted after config changes

Platform not supported:
1. Check platform status from UniversalDL platforms page
2. Some platform capabilities depend on plan and content accessibility

Download starts but file does not arrive:
1. Verify Celery worker is running
2. Start worker with:

```bash
celery -A celery_worker.celery worker --loglevel=info
```

## Privacy
The extension communicates only with your configured UniversalDL server.
No download data is sent to third party analytics services by this extension.
Download history remains on your own server environment.
