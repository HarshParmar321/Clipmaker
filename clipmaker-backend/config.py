import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "./storage")).resolve()
UPLOADS_DIR = STORAGE_DIR / "uploads"
DOWNLOADS_DIR = STORAGE_DIR / "downloads"
CLIPS_DIR = STORAGE_DIR / "clips"

for d in (UPLOADS_DIR, DOWNLOADS_DIR, CLIPS_DIR):
    d.mkdir(parents=True, exist_ok=True)
