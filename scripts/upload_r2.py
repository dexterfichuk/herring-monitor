#!/usr/bin/env python3
"""Upload monitor + sweep images to Cloudflare R2 bucket."""
import sqlite3, os, subprocess, time, json
from pathlib import Path

BUCKET = "herring-monitor-images"
MONITOR_DIR = Path("/Volumes/Z Slim/herring-spawn-data/monitor")
SWEEP_DIR = Path("/Volumes/Z Slim/herring-spawn-data/candidates_sweep_2025")
DB = MONITOR_DIR / "monitor.db"

done = 0
errors = 0

def upload(src_path, key):
    global done, errors
    if not os.path.exists(src_path):
        errors += 1; return
    r = subprocess.run(
        ["wrangler", "r2", "object", "put", f"{BUCKET}/{key}", "--file", src_path],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode == 0:
        done += 1
    else:
        errors += 1
        if errors <= 3:
            print(f"  error: {r.stderr[:200]}")

# ── Upload monitor images ──
db = sqlite3.connect(str(DB))
db.row_factory = sqlite3.Row
images = db.execute("SELECT filename FROM images WHERE filename IS NOT NULL").fetchall()
db.close()

print(f"Uploading {len(images)} monitor images...")
for i, img in enumerate(images):
    src = MONITOR_DIR / img["filename"]
    upload(str(src), img["filename"])
    if (i+1) % 200 == 0:
        print(f"  [{i+1}/{len(images)}] uploaded={done} errors={errors}", flush=True)
    time.sleep(0.1)

print(f"\nMonitor done: {done} uploaded, {errors} errors")

# ── Upload sweep images ──
man = SWEEP_DIR / "manifest.json"
if man.exists():
    with open(man) as f: sweep = json.load(f)
    print(f"\nUploading {len(sweep)} sweep images...")
    start = done
    for i, e in enumerate(sweep):
        fn = e.get("filename")
        if fn:
            upload(str(SWEEP_DIR / fn), fn)
        if (i+1) % 200 == 0:
            print(f"  [{i+1}/{len(sweep)}] uploaded={done-start} errors={errors}", flush=True)
        time.sleep(0.1)
    print(f"Sweep done: {done-start} new uploads")

print(f"\nTotal: {done} images uploaded, {errors} errors")
