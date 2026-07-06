import os, sys, traceback

try:
    from huggingface_hub import HfApi
    token = os.environ.get("HF_TOKEN", "")
    print("HF_TOKEN length:", len(token))
    print("HF_TOKEN prefix:", token[:10] if token else "EMPTY")
    
    api = HfApi(token=token)
    
    # Test connection first
    try:
        user = api.whoami()
        print("Logged in as:", user.get("name","unknown"))
    except Exception as e:
        print("Auth error:", e)
        sys.exit(1)
    
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
        if val and "REPLACE" not in val:
            api.add_space_secret(repo_id=repo_id, key=key, value=val)
            print(f"Secret: {key}")
    
    print("Done! https://huggingface.co/spaces/ayankandar2/manga-colorizer-bot")

except Exception as e:
    print("ERROR:", e)
    traceback.print_exc()
    sys.exit(1)