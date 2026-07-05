"""
Manga Colorizer Bot
====================
Telegram → Gemini → Telegram

Flow:
1. User sends manga files (PDF, CBZ, ZIP, JPG, PNG)
2. Bot queues all files
3. Processes one by one: sends to Gemini for colorization
4. Sends colorized result to output channel
5. Same format as input (PDF→PDF, CBZ→CBZ, ZIP→ZIP)
"""

import os
import io
import json
import time
import logging
import zipfile
import tempfile
import threading
import mimetypes
import requests
from queue import Queue
from pathlib import Path
from unzip_utils import extract_all, repack_images, output_filename

# ── CONFIG — replace these values ────────────────────────────────────
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID   = "@autoanime464"          # output channel
GEMINI_API_KEY     = "YOUR_GEMINI_API_KEY_HERE"
# ─────────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.0-flash-preview-image-generation"
GEMINI_URL   = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
TG_API       = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TG_FILE_API  = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"
MAX_TG_SIZE  = 49 * 1024 * 1024  # 49MB Telegram limit

COLORIZE_PROMPT = """You are colorizing a black-and-white manga/manhwa page.

Strict rules:
1. Keep ALL original linework, panel layout, speech bubbles, text exactly the same
2. Do NOT change character designs, proportions or any artwork
3. Add vibrant, professional manhwa-style colors — bright, saturated, clean
4. Use proper skin tones, realistic hair colors, detailed clothing colors
5. Make it look like a professionally colored Korean manhwa (Solo Leveling style)
6. Return ONLY the colorized image, nothing else"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Global file queue — processes one file at a time
file_queue = Queue()
sessions   = {}  # { user_id: { state, ... } }


# ── Telegram helpers ──────────────────────────────────────────────────

def tg_send(chat_id, text, markup=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup:
        p["reply_markup"] = json.dumps(markup)
    r = requests.post(f"{TG_API}/sendMessage", json=p).json()
    return r.get("result", {}).get("message_id")


def tg_edit(chat_id, msg_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if markup:
        p["reply_markup"] = json.dumps(markup)
    requests.post(f"{TG_API}/editMessageText", json=p)


def tg_delete(chat_id, msg_id):
    requests.post(f"{TG_API}/deleteMessage", json={"chat_id": chat_id, "message_id": msg_id})


def tg_answer(cb_id):
    requests.post(f"{TG_API}/answerCallbackQuery", json={"callback_query_id": cb_id})


def tg_send_document(chat_id, file_path, caption=""):
    size = os.path.getsize(file_path)
    if size > MAX_TG_SIZE:
        tg_send(chat_id,
            f"\u26a0\ufe0f File too large ({size//1024//1024}MB > 49MB).\n"
            f"\U0001f4c4 {os.path.basename(file_path)}"
        )
        return
    with open(file_path, "rb") as f:
        requests.post(f"{TG_API}/sendDocument", data={
            "chat_id": chat_id, "caption": caption, "parse_mode": "HTML"
        }, files={"document": f})


def kb(*rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def tg_get_file_url(file_id):
    r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}).json()
    return f"{TG_FILE_API}/{r['result']['file_path']}"


def tg_download_file(file_id, dest_path):
    url = tg_get_file_url(file_id)
    r   = requests.get(url, stream=True)
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)


# ── Gemini colorization ───────────────────────────────────────────────

def image_to_base64(image_path):
    with open(image_path, "rb") as f:
        return __import__("base64").b64encode(f.read()).decode()


def get_mime(path):
    mime, _ = mimetypes.guess_type(path)
    return mime or "image/jpeg"


def colorize_image(image_path):
    """Send one image to Gemini and return colorized bytes."""
    import base64

    b64 = image_to_base64(image_path)
    mime = get_mime(image_path)

    payload = {
        "contents": [{
            "parts": [
                {"text": COLORIZE_PROMPT},
                {"inline_data": {"mime_type": mime, "data": b64}}
            ]
        }],
        "generationConfig": {"responseModalities": ["image", "text"]}
    }

    r = requests.post(GEMINI_URL, json=payload, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:200]}")

    data = r.json()
    for part in data["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            return base64.b64decode(part["inlineData"]["data"])

    raise RuntimeError("Gemini returned no image")


# ── Format handling ───────────────────────────────────────────────────

def extract_pages(input_path, dest_dir):
    """Extract all pages from file as images."""
    ext = Path(input_path).suffix.lower()
    pages = []

    if ext in (".cbz", ".zip"):
        with zipfile.ZipFile(input_path) as z:
            z.extractall(dest_dir)
        for f in sorted(os.listdir(dest_dir)):
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                pages.append(os.path.join(dest_dir, f))

    elif ext == ".pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(input_path)
            for i, page in enumerate(doc):
                pix  = page.get_pixmap(dpi=150)
                path = os.path.join(dest_dir, f"{i+1:03d}.png")
                pix.save(path)
                pages.append(path)
        except ImportError:
            raise RuntimeError("PDF support needs PyMuPDF: pip install PyMuPDF")

    elif ext in (".jpg", ".jpeg", ".png", ".webp"):
        import shutil
        dest = os.path.join(dest_dir, os.path.basename(input_path))
        shutil.copy(input_path, dest)
        pages.append(dest)

    else:
        # Try as zip
        try:
            with zipfile.ZipFile(input_path) as z:
                z.extractall(dest_dir)
            for f in sorted(os.listdir(dest_dir)):
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    pages.append(os.path.join(dest_dir, f))
        except Exception:
            raise RuntimeError(f"Unsupported format: {ext}")

    return pages


def repack(colored_dir, original_path, output_path):
    """Repack colorized pages into same format as input."""
    ext = Path(original_path).suffix.lower()

    if ext in (".cbz", ".zip", ".cbr"):
        out_ext = ".cbz" if ext == ".cbr" else ext
        out = output_path.replace(ext, out_ext)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(os.listdir(colored_dir)):
                z.write(os.path.join(colored_dir, f), arcname=f)
        return out

    elif ext == ".pdf":
        try:
            import img2pdf
            pages = sorted(
                os.path.join(colored_dir, f)
                for f in os.listdir(colored_dir)
                if f.lower().endswith(".png")
            )
            with open(output_path, "wb") as f:
                f.write(img2pdf.convert(pages))
        except ImportError:
            from PIL import Image
            imgs = [Image.open(os.path.join(colored_dir, f)).convert("RGB")
                    for f in sorted(os.listdir(colored_dir)) if f.endswith(".png")]
            if imgs:
                imgs[0].save(output_path, "PDF", save_all=True, append_images=imgs[1:])
        return output_path

    elif ext in (".jpg", ".jpeg", ".png", ".webp"):
        pages = sorted(os.listdir(colored_dir))
        if pages:
            import shutil
            shutil.copy(os.path.join(colored_dir, pages[0]), output_path)
        return output_path

    else:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(os.listdir(colored_dir)):
                z.write(os.path.join(colored_dir, f), arcname=f)
        return output_path


def output_name(original_name):
    base, ext = os.path.splitext(original_name)
    if ext.lower() == ".cbr":
        ext = ".cbz"
    return base + "_colored" + ext


# ── Queue processor ───────────────────────────────────────────────────

def process_queue():
    """Runs in background — processes one file at a time."""
    while True:
        item = file_queue.get()
        try:
            _process_file(item)
        except Exception as e:
            log.error(f"Queue error: {e}", exc_info=True)
            try:
                tg_send(item["chat_id"], f"\u274c Error processing {item['file_name']}: {e}")
            except Exception:
                pass
        finally:
            file_queue.task_done()


def _process_file(item):
    chat_id   = item["chat_id"]
    file_id   = item["file_id"]
    file_name = item["file_name"]
    queue_pos = item["queue_pos"]
    total     = item["total"]

    log.info(f"Processing [{queue_pos}/{total}]: {file_name}")

    status_id = tg_send(chat_id,
        f"\u2699\ufe0f <b>Processing [{queue_pos}/{total}]</b>\n"
        f"\U0001f4c4 {file_name}\n\n"
        f"\u23f3 Downloading..."
    )

    with tempfile.TemporaryDirectory() as tmp:
        # Download file
        input_path = os.path.join(tmp, file_name)
        tg_download_file(file_id, input_path)

        tg_edit(chat_id, status_id,
            f"\u2699\ufe0f <b>Processing [{queue_pos}/{total}]</b>\n"
            f"\U0001f4c4 {file_name}\n\n"
            f"\U0001f3a8 Colorizing pages..."
        )

        # Extract all images from archive (ZIP, CBZ, CBR, PDF, RAR, 7z, single image)
        raw_dir = os.path.join(tmp, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        pages = extract_all(input_path, raw_dir)
        log.info(f"Extracted {len(pages)} pages from {file_name}")

        tg_edit(chat_id, status_id,
            f"\u2699\ufe0f <b>Processing [{queue_pos}/{total}]</b>\n"
            f"\U0001f4c4 {file_name}\n"
            f"\U0001f4f1 {len(pages)} page(s) found\n\n"
            f"\U0001f3a8 Colorizing..."
        )

        # Colorize each page
        colored_dir = os.path.join(tmp, "colored")
        os.makedirs(colored_dir, exist_ok=True)

        for i, page_path in enumerate(pages, 1):
            tg_edit(chat_id, status_id,
                f"\u2699\ufe0f <b>Processing [{queue_pos}/{total}]</b>\n"
                f"\U0001f4c4 {file_name}\n"
                f"\U0001f3a8 Colorizing page {i}/{len(pages)}...\n\n"
                f"[{'\u2593' * i}{'\u2591' * (len(pages) - i)}]"
            )
            colored_bytes = colorize_image(page_path)
            out_path = os.path.join(colored_dir, f"{i:04d}.png")
            with open(out_path, "wb") as f:
                f.write(colored_bytes)
            time.sleep(1)  # avoid Gemini rate limit

        # Repack into same format as input
        out_name   = output_filename(file_name)
        out_path   = os.path.join(tmp, out_name)
        final_path = repack_images(colored_dir, input_path, out_path)

        tg_edit(chat_id, status_id,
            f"\u2714\ufe0f <b>Done [{queue_pos}/{total}]</b>\n"
            f"\U0001f4c4 {file_name}\n\n"
            f"\U0001f4e4 Sending to channel..."
        )

        # Send to output channel
        caption = (
            f"\U0001f3a8 <b>Colorized!</b>\n"
            f"\U0001f4c4 {out_name}\n"
            f"\U0001f4ca {len(pages)} pages"
        )
        tg_send_document(TELEGRAM_CHAT_ID, final_path, caption)

        # Update status
        tg_edit(chat_id, status_id,
            f"\u2705 <b>Complete [{queue_pos}/{total}]</b>\n"
            f"\U0001f4c4 {file_name}\n\n"
            f"\U0001f4e4 Sent to {TELEGRAM_CHAT_ID}!"
        )

        if queue_pos == total:
            tg_send(chat_id,
                f"\U0001f389 <b>All {total} file(s) done!</b>\n"
                f"Check {TELEGRAM_CHAT_ID} for results."
            )


# ── Batch file collector ──────────────────────────────────────────────

batch_buffer = {}
BATCH_WINDOW = 3.0


def _flush_batch(user_id):
    buf = batch_buffer.pop(user_id, None)
    if not buf:
        return

    files   = buf["files"]
    chat_id = buf["chat_id"]
    total   = len(files)

    # Build preview
    preview = "\n".join(f"  \U0001f4c4 {f['file_name']}" for f in files[:5])
    if total > 5:
        preview += f"\n  ... and {total - 5} more"

    tg_send(chat_id,
        f"\U0001f4e5 <b>{total} file(s) queued!</b>\n\n"
        f"{preview}\n\n"
        f"\u23f3 Processing one by one...\n"
        f"Results will be sent to {TELEGRAM_CHAT_ID}"
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


# ── Message handlers ──────────────────────────────────────────────────

def handle_file(update):
    msg     = update["message"]
    chat_id = msg["chat"]["id"]
    user_id = str(chat_id)
    doc     = msg.get("document") or msg.get("video") or msg.get("audio")
    if not doc:
        return

    file_id   = doc["file_id"]
    file_name = doc.get("file_name", f"file_{int(time.time())}")

    queue_file(user_id, chat_id, file_id, file_name)


def handle_text(update):
    msg     = update["message"]
    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()

    if text == "/start":
        tg_send(chat_id,
            "\U0001f3a8 <b>Manga Colorizer Bot</b>\n\n"
            "Send me manga files and I'll colorize them!\n\n"
            "\u0031\ufe0f\u20e3 Send files (PDF, CBZ, ZIP, JPG)\n"
            "\u0032\ufe0f\u20e3 Files processed one by one\n"
            "\u0033\ufe0f\u20e3 Colored results sent to channel\n\n"
            "Powered by \u2728 Gemini AI\n\n"
            "Just send your files to start!"
        )
    elif text == "/status":
        qsize = file_queue.qsize()
        tg_send(chat_id,
            f"\U0001f4ca <b>Bot Status</b>\n\n"
            f"\u2705 Online and running!\n"
            f"\U0001f4e5 Queue: <b>{qsize}</b> file(s) waiting\n"
            f"\U0001f916 Powered by Gemini AI\n"
            f"\U0001f4e4 Output: {TELEGRAM_CHAT_ID}"
        )
    else:
        tg_send(chat_id, "\U0001f4e4 Send me manga files to colorize!\nOr /start for help.")


# ── Poll loop ─────────────────────────────────────────────────────────

def poll():
    log.info("Bot started!")
    offset = 0
    while True:
        try:
            r = requests.get(f"{TG_API}/getUpdates",
                params={"offset": offset, "timeout": 30}, timeout=35).json()
            for update in r.get("result", []):
                offset = update["update_id"] + 1
                try:
                    if "message" in update:
                        msg = update["message"]
                        if any(k in msg for k in ("document", "video", "audio")):
                            handle_file(update)
                        elif "text" in msg:
                            handle_text(update)
                except Exception as e:
                    log.error(f"Update error: {e}", exc_info=True)
        except Exception as e:
            log.error(f"Poll error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    # Validate config
    if "YOUR_" in TELEGRAM_BOT_TOKEN:
        print("❌ Please set TELEGRAM_BOT_TOKEN!")
        exit(1)
    if "YOUR_" in GEMINI_API_KEY:
        print("❌ Please set GEMINI_API_KEY!")
        exit(1)

    # Start queue processor in background
    t = threading.Thread(target=process_queue, daemon=True)
    t.start()
    log.info("Queue processor started!")

    # Start polling
    poll()
