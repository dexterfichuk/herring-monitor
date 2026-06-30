"""Herring Monitor — Flask web app for monitoring spawn locations via Sentinel-2."""

from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory
from pathlib import Path
import json, os, datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Config
app.config["IMAGE_DIR"] = Path(os.environ.get("IMAGE_DIR", "/Volumes/Z Slim/herring-spawn-data/monitor"))
app.config["DB_PATH"] = app.config["IMAGE_DIR"] / "monitor.db"

# Init DB
from web.db import init_db, get_db
init_db(app.config["DB_PATH"])


@app.route("/")
def dashboard():
    """Main monitoring dashboard — grid of latest images per location."""
    db = get_db(app.config["DB_PATH"])
    locations = db.execute(
        "SELECT l.*, (SELECT filename FROM images WHERE location_id=l.id ORDER BY scene_date DESC LIMIT 1) as latest_image, (SELECT scene_date FROM images WHERE location_id=l.id ORDER BY scene_date DESC LIMIT 1) as latest_date FROM locations l ORDER BY l.region, l.name"
    ).fetchall()
    return render_template("dashboard.html", locations=locations)


@app.route("/location/<int:loc_id>")
def location_detail(loc_id):
    """History view — all images for a single location."""
    db = get_db(app.config["DB_PATH"])
    loc = db.execute("SELECT * FROM locations WHERE id=?", (loc_id,)).fetchone()
    if not loc:
        return "Not found", 404
    images = db.execute(
        "SELECT * FROM images WHERE location_id=? ORDER BY scene_date DESC", (loc_id,)
    ).fetchall()
    return render_template("location.html", location=loc, images=images)


@app.route("/sweep")
def sweep():
    """Candidate review — auto-found spots to label."""
    db = get_db(app.config["DB_PATH"])
    candidates = db.execute(
        "SELECT * FROM candidates WHERE label IS NULL ORDER BY knn_score DESC"
    ).fetchall()
    return render_template("sweep.html", candidates=candidates)


@app.route("/add", methods=["GET", "POST"])
def add_location():
    """Add a new location to monitor."""
    if request.method == "POST":
        db = get_db(app.config["DB_PATH"])
        db.execute(
            "INSERT INTO locations (name, region, lat, lon, created_at) VALUES (?,?,?,?,datetime('now'))",
            (request.form["name"], request.form["region"],
             float(request.form["lat"]), float(request.form["lon"]))
        )
        db.commit()
        return redirect(url_for("dashboard"))
    return render_template("add.html")


@app.route("/api/refresh", methods=["POST"])
def refresh_images():
    """Check all monitored locations for new S2 scenes. Called via cron or manual."""
    from web.fetcher import fetch_latest_for_all
    db = get_db(app.config["DB_PATH"])
    added = fetch_latest_for_all(db, app.config["IMAGE_DIR"])
    return jsonify({"status": "ok", "added": added})


@app.route("/api/candidates/<int:cand_id>/label", methods=["POST"])
def label_candidate(cand_id):
    """Label a sweep candidate as spawn/no-spawn/skip."""
    data = request.get_json()
    db = get_db(app.config["DB_PATH"])
    db.execute("UPDATE candidates SET label=?, labeled_at=datetime('now') WHERE id=?",
               (data["label"], cand_id))
    db.commit()
    return jsonify({"status": "ok"})


@app.route("/api/candidates/<int:cand_id>/promote", methods=["POST"])
def promote_candidate(cand_id):
    """Promote a labeled spawn candidate to a monitored location."""
    db = get_db(app.config["DB_PATH"])
    cand = db.execute("SELECT * FROM candidates WHERE id=?", (cand_id,)).fetchone()
    if not cand:
        return jsonify({"error": "not found"}), 404
    db.execute(
        "INSERT INTO locations (name, region, lat, lon, source, created_at) VALUES (?,?,?,?,?,datetime('now'))",
        (f"Candidate {cand_id}", "Sweep", cand["lat"], cand["lon"], "sweep")
    )
    loc_id = db.lastrowid
    db.execute("UPDATE candidates SET promoted_to_location_id=?, label='spawn' WHERE id=?",
               (loc_id, cand_id))
    db.commit()
    return jsonify({"status": "ok", "location_id": loc_id})


@app.route("/images/<path:filename>")
def serve_image(filename):
    """Serve downloaded S2 thumbnails."""
    return send_from_directory(str(app.config["IMAGE_DIR"]), filename)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Simple password check (placeholder)
        if request.form["password"] == os.environ.get("APP_PASSWORD", "herring"):
            from flask import session
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Wrong password")
    return render_template("login.html")


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    app.run(host="0.0.0.0", port=port, debug=True)
