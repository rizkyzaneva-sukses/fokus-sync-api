"""Launcher: baca .env lalu start uvicorn. Hindari shell redaction."""
import os, sys, subprocess
from pathlib import Path

os.chdir(Path(__file__).parent)

# Baca .env manual
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

print("DB URL:", os.environ.get("DATABASE_URL", "")[:40] + "...", flush=True)
print("SYNC_KEY:", os.environ.get("SYNC_KEY", "")[:10] + "...", flush=True)

sys.exit(subprocess.call([
    sys.executable, "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1", "--port", "8111",
]))
