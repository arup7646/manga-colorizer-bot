# Manga Colorizer Bot

Telegram bot that colorizes manga using Gemini AI.

## How it works
1. Send manga files to bot (PDF, CBZ, ZIP, JPG)
2. Files processed one by one via Gemini AI
3. Colored results sent to @autoanime464 channel

## Setup

### Step 1 — Edit bot.py
Replace these two values in bot.py:
```python
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
GEMINI_API_KEY     = "YOUR_GEMINI_API_KEY_HERE"
```

### Step 2 — Deploy to Google Cloud Run

```bash
# 1. Install Google Cloud CLI
# 2. Login
gcloud auth login

# 3. Set project
gcloud config set project YOUR_PROJECT_ID

# 4. Enable Cloud Run
gcloud services enable run.googleapis.com

# 5. Build and deploy
gcloud run deploy manga-colorizer-bot \
  --source . \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --memory 512Mi \
  --timeout 3600
```

## Commands
- `/start` — show help
- `/status` — show queue status
