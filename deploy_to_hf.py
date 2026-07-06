import os
from huggingface_hub import HfApi

api       = HfApi(token=os.environ["HF_TOKEN"])
repo_id   = "ayankandar2/manga-colorizer-bot"
repo_type = "space"

files = ["bot.py", "unzip_utils.py", "Dockerfile", "requirements.txt", "README.md"]
for f in files:
    if os.path.exists(f):
        api.upload_file(path_or_fileobj=f, path_in_repo=f, repo_id=repo_id, repo_type=repo_type)
        print(f"Uploaded: {f}")

for key, val in {
    "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN",""),
    "GEMINI_API_KEY":     os.environ.get("GEMINI_API_KEY",""),
    "TELEGRAM_CHAT_ID":   os.environ.get("TELEGRAM_CHAT_ID",""),
}.items():
    if val:
        api.add_space_secret(repo_id=repo_id, key=key, value=val)
        print(f"Secret set: {key}")

print("Done! https://huggingface.co/spaces/ayankandar2/manga-colorizer-bot")