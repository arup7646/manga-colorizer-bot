"""
Manga Colorizer Bot - Webhook mode for Hugging Face Spaces
HF Spaces blocks outgoing connections, so we use webhook instead of polling.
Telegram sends updates TO us via HTTP POST.
"""

import os
import json
import time
import logging
import tempfile
import threading
import mimetypes
import requests
from queue import Queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from unzip_utils import extract_all, repack_images, output_filename

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "@autoanime464")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODEL = "gemini-2.0-flash-preview-image-generation"
GEMINI_URL   = ("https://generativelanguage.googleapis.com/v1beta/models/"
                + GEMINI_MODEL + ":generateContent")
TG_API       = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN
TG_FILE_API  = "https://api.telegram.org/file/bot" + TELEGRAM_BOT_TOKEN
MAX_TG_SIZE  = 49 * 1024 * 1024

COLORIZE_PROMPT = (
    "You are colorizing a black-and-white manga/manhwa page.\n\n"
    "Rules:\n"
    "1. Keep ALL original linework, panels, speech bubbles, text exactly the same\n"
    "2. Add vibrant professional manhwa-style colors - bright, saturated, clean\n"
    "3. Use proper skin tones, hair colors, clothing colors\n"
    "4. Make it look like professionally colored Korean manhwa (Solo Leveling style)\n"
    "5. Return ONLY the colorized image"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

file_queue   = Queue()
batch_buffer = {}
BATCH_WINDOW = 3.0


# ── Telegram helpers ──────────────────────────────────────────────────

def tg_post(endpoint, data=None, files=None):
    try:
        if files:
            return requests.post(TG_API + endpoint, data=data, files=files, timeout=60)
        return requests.post(TG_API + endpoint, json=data, timeout=30)
    except Exception as e:
        log.error("TG error: " + str(e))
        return None


def tg_send(chat_id, text, markup=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup:
        p["reply_markup"] = json.dumps(markup)
    r = tg_post("/sendMessage", p)
    if r and r.ok:
        return r.json().get("result", {}).get("message_id")
    return None


def tg_edit(chat_id, msg_id, text):
    tg_post("/editMessageText", {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": text,
        "parse_mode": "HTML"
    })


def tg_send_doc(chat_id, file_path, caption=""):
    size = os.path.getsize(file_path)
    if size > MAX_TG_SIZE:
        tg_send(chat_id,
            "File too large (" + str(size // 1024 // 1024) + "MB > 49MB).\n"
            + os.path.basename(file_path)
        )
        return
    with open(file_path, "rb") as f:
        tg_post("/sendDocument",
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"document": f}
        )


def tg_download(file_id, dest_path):
    r   = requests.get(TG_API + "/getFile", params={"file_id": file_id}, timeout=30).json()
    url = TG_FILE_API + "/" + r["result"]["file_path"]
    r2  = requests.get(url, stream=True, timeout=120)
    with open(dest_path, "wb") as f:
        for chunk in r2.iter_content(8192):
            f.write(chunk)


# ── Gemini colorization ───────────────────────────────────────────────

def colorize_image(image_path):
    import base64
    with open(image_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode()
    mime, _ = mimetypes.guess_type(image_path)
    mime = mime or "image/jpeg"
    payload = {
        "contents": [{"parts": [
            {"text": COLORIZE_PROMPT},
            {"inline_data": {"mime_type": mime, "data": img_data}}
        ]}],
        "generationConfig": {"responseModalities": ["image", "text"]}
    }
    r = requests.post(GEMINI_URL, json=payload, timeout=120,
                      headers={"x-goog-api-key": GEMINI_API_KEY})
    if r.status_code != 200:
        raise RuntimeError("Gemini error " + str(r.status_code) + ": " + r.text[:200])
    import base64 as b64
    for part in r.json()["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            return b64.b64decode(part["inlineData"]["data"])
    raise RuntimeError("Gemini returned no image")


# ── Queue processor ───────────────────────────────────────────────────

def process_queue():
    while True:
        item = file_queue.get()
        try:
            _process_file(item)
        except Exception as e:
            log.error("Queue error: " + str(e), exc_info=True)
            try:
                tg_send(item["chat_id"], "Error processing " + item["file_name"] + ": " + str(e))
            except Exception:
                pass
        finally:
            file_queue.task_done()


def _process_file(item):
    chat_id   = item["chat_id"]
    file_id   = item["file_id"]
    file_name = item["file_name"]
    pos       = item["queue_pos"]
    total     = item["total"]

    log.info("Processing [" + str(pos) + "/" + str(total) + "]: " + file_name)

    status_id = tg_send(chat_id,
        "<b>Processing [" + str(pos) + "/" + str(total) + "]</b>\n"
        + file_name + "\n\nDownloading..."
    )

    with tempfile.TemporaryDirectory() as tmp:
        input_path = os.path.join(tmp, file_name)
        tg_download(file_id, input_path)

        if status_id:
            tg_edit(chat_id, status_id,
                "<b>Processing [" + str(pos) + "/" + str(total) + "]</b>\n"
                + file_name + "\n\nExtracting pages..."
            )

        raw_dir = os.path.join(tmp, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        pages = extract_all(input_path, raw_dir)
        log.info("Extracted " + str(len(pages)) + " pages")

        colored_dir = os.path.join(tmp, "colored")
        os.makedirs(colored_dir, exist_ok=True)

        for i, page_path in enumerate(pages, 1):
            bar = "#" * i + "-" * (len(pages) - i)
            if status_id:
                tg_edit(chat_id, status_id,
                    "<b>Colorizing [" + str(pos) + "/" + str(total) + "]</b>\n"
                    + file_name + "\n"
                    + "Page " + str(i) + "/" + str(len(pages)) + "\n"
                    + "[" + bar + "]"
                )
            colored_bytes = colorize_image(page_path)
            out_path = os.path.join(colored_dir, str(i).zfill(4) + ".png")
            with open(out_path, "wb") as f:
                f.write(colored_bytes)
            time.sleep(1)

        out_name   = output_filename(file_name)
        out_path   = os.path.join(tmp, out_name)
        final_path = repack_images(colored_dir, input_path, out_path)

        tg_send_doc(TELEGRAM_CHAT_ID, final_path,
            "<b>Colorized!</b>\n" + out_name + "\n" + str(len(pages)) + " pages"
        )

        if status_id:
            tg_edit(chat_id, status_id,
                "<b>Done [" + str(pos) + "/" + str(total) + "]</b>\n"
                + file_name + "\n\nSent to " + TELEGRAM_CHAT_ID + "!"
            )

        if pos == total:
            tg_send(chat_id,
                "All " + str(total) + " file(s) done!\nCheck " + TELEGRAM_CHAT_ID
            )


# ── Batch collector ───────────────────────────────────────────────────

def _flush_batch(user_id):
    buf = batch_buffer.pop(user_id, None)
    if not buf:
        return
    files   = buf["files"]
    chat_id = buf["chat_id"]
    total   = len(files)
    preview = "\n".join("- " + f["file_name"] for f in files[:5])
    if total > 5:
        preview += "\n... and " + str(total - 5) + " more"
    tg_send(chat_id,
        str(total) + " file(s) queued!\n\n"
        + preview + "\n\n"
        + "Processing one by one...\nResults -> " + TELEGRAM_CHAT_ID
    )
    for i, f in enumerate(files, 1):
        file_queue.put({
            "chat_id":   chat_id,
            "file_id":   f["file_id"],
            "file_name": f["file_name"],
            "queue_pos": i,
            "total":     total,
        })


def queue_file(user_id, chat_id, file_id, file_name):
    if user_id in batch_buffer:
        batch_buffer[user_id]["timer"].cancel()
    else:
        batch_buffer[user_id] = {"files": [], "chat_id": chat_id}
    batch_buffer[user_id]["files"].append({"file_id": file_id, "file_name": file_name})
    t = threading.Timer(BATCH_WINDOW, _flush_batch, args=[user_id])
    t.daemon = True
    t.start()
    batch_buffer[user_id]["timer"] = t


# ── Update handler ────────────────────────────────────────────────────

def handle_update(update):
    try:
        if "message" not in update:
            return
        msg     = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = str(chat_id)

        if any(k in msg for k in ("document", "video", "audio")):
            doc       = msg.get("document") or msg.get("video") or msg.get("audio")
            file_id   = doc["file_id"]
            file_name = doc.get("file_name", "file_" + str(int(time.time())))
            queue_file(user_id, chat_id, file_id, file_name)

        elif "text" in msg:
            text = msg["text"].strip()
            if text == "/start":
                tg_send(chat_id,
                    "<b>Manga Colorizer Bot</b>\n\n"
                    "Send manga files to colorize!\n\n"
                    "Supported: PDF, CBZ, CBR, ZIP, JPG, PNG\n\n"
                    "/ping - check bot is alive\n"
                    "/status - queue status"
                )
            elif text == "/ping":
                tg_send(chat_id,
                    "Bot is alive!\n"
                    "Queue: " + str(file_queue.qsize()) + " file(s)\n"
                    "Output: " + TELEGRAM_CHAT_ID
                )
            elif text == "/status":
                tg_send(chat_id,
                    "Online: Yes\n"
                    "Queue: " + str(file_queue.qsize()) + " file(s)\n"
                    "Output: " + TELEGRAM_CHAT_ID
                )
            else:
                tg_send(chat_id, "Send manga files or /start for help.")
    except Exception as e:
        log.error("Handle update error: " + str(e), exc_info=True)


# ── Webhook HTTP server ───────────────────────────────────────────────

class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Manga Colorizer Bot is running!")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        try:
            update = json.loads(body)
            threading.Thread(target=handle_update, args=[update], daemon=True).start()
        except Exception as e:
            log.error("Webhook parse error: " + str(e))

    def log_message(self, format, *args):
        pass


def set_webhook(webhook_url):
    r = requests.post(TG_API + "/setWebhook",
                      json={"url": webhook_url, "drop_pending_updates": True},
                      timeout=30)
    result = r.json()
    log.info("Webhook result: " + str(result))
    return result.get("ok", False)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set!")
        exit(1)
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not set!")
        exit(1)

    # Start queue processor thread
    t = threading.Thread(target=process_queue, daemon=True)
    t.start()
    log.info("Queue processor started")

    # Set webhook using HF Space host
    port       = int(os.environ.get("PORT", 7860))
    space_host = os.environ.get("SPACE_HOST", "")

    if space_host:
        webhook_url = "https://" + space_host
        log.info("Setting webhook to: " + webhook_url)
        ok = set_webhook(webhook_url)
        if ok:
            log.info("Webhook set successfully!")
        else:
            log.warning("Webhook setup failed - bot may not receive messages")
    else:
        log.warning("SPACE_HOST not set - webhook not configured")

    # Start HTTP server
    log.info("Starting server on port " + str(port))
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
