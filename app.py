"""Herring Monitor — Flask web app for monitoring spawn locations via Sentinel-2."""

from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory
from pathlib import Path
import json, os, datetime, sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Config
default_dir = Path(os.path.dirname(__file__)) / "data" / "images"
app.config["IMAGE_DIR"] = Path(os.environ.get("IMAGE_DIR", str(default_dir)))
app.config["DB_PATH"] = app.config["IMAGE_DIR"] / "monitor.db"

# Image CDN base URL (R2 bucket or local filesystem)
app.config["IMAGE_BASE_URL"] = os.environ.get("IMAGE_BASE_URL", "")

# Init DB — seed from bundled copy on Render if empty
from web.db import init_db, get_db
app.config["IMAGE_DIR"].mkdir(parents=True, exist_ok=True)
init_db(app.config["DB_PATH"])

# Seed from bundled DB if images are missing (locations may already exist from prior deploy)
db = get_db(app.config["DB_PATH"])
image_count = db.execute("SELECT COUNT(*) FROM images").fetchone()[0]
if image_count == 0:
    seed_db = Path(__file__).parent / "seed.db"
    if seed_db.exists():
        print("Seeding from bundled seed.db...")
        db.execute("ATTACH ? AS seed_db", (str(seed_db),))
        for table in ["locations", "images", "candidates"]:
            try:
                db.execute(f"INSERT OR IGNORE INTO main.{table} SELECT * FROM seed_db.{table}")
                db.commit()
            except Exception as e:
                print(f"  Seed error ({table}): {e}")
        db.execute("DETACH seed_db")
        new_imgs = db.execute("SELECT COUNT(*) FROM main.images").fetchone()[0]
        print(f"Seed complete: {new_imgs} images, {db.execute('SELECT COUNT(*) FROM main.locations').fetchone()[0]} locations")


@app.route("/")
def dashboard():
    """Main monitoring dashboard — grid of latest images per location."""
    db = get_db(app.config["DB_PATH"])
    year = request.args.get("year", "")
    spawn_min = request.args.get("spawn", "")
    year_clause = "AND scene_date LIKE :year" if year else ""
    spawn_clause = "AND COALESCE(spawn_score,0) >= :spawn" if spawn_min else ""
    params = {}
    if year: params["year"] = f"{year}%"
    if spawn_min: params["spawn"] = float(spawn_min)

    where_clause = ""
    if spawn_min:
        where_clause = f"WHERE EXISTS (SELECT 1 FROM images WHERE location_id=l.id {spawn_clause})"

    query = f"""
        SELECT l.*,
            (SELECT filename FROM images WHERE location_id=l.id {year_clause} {spawn_clause}
             ORDER BY COALESCE(cloud_cover,100) ASC, scene_date DESC LIMIT 1) as latest_image,
            (SELECT scene_date FROM images WHERE location_id=l.id {year_clause} {spawn_clause}
             ORDER BY COALESCE(cloud_cover,100) ASC, scene_date DESC LIMIT 1) as latest_date,
            (SELECT cloud_cover FROM images WHERE location_id=l.id {year_clause} {spawn_clause}
             ORDER BY COALESCE(cloud_cover,100) ASC, scene_date DESC LIMIT 1) as cloud_cover,
            (SELECT spawn_score FROM images WHERE location_id=l.id {year_clause} {spawn_clause}
             ORDER BY COALESCE(cloud_cover,100) ASC, scene_date DESC LIMIT 1) as spawn_score,
            (SELECT COUNT(*) FROM images WHERE location_id=l.id {year_clause} {spawn_clause}) as image_count
        FROM locations l
        {where_clause}
        ORDER BY COALESCE(
            (SELECT spawn_score FROM images WHERE location_id=l.id {year_clause} {spawn_clause}
             ORDER BY COALESCE(cloud_cover,100) ASC LIMIT 1), 0) DESC, l.region, l.name
    """
    locations = db.execute(query, params).fetchall()
    locs = [dict(l) for l in locations]
    years = db.execute("SELECT DISTINCT substr(scene_date,1,4) as yr FROM images ORDER BY yr DESC").fetchall()
    avail_years = [y["yr"] for y in years]
    return render_template("dashboard.html", locations=locs, years=avail_years, selected_year=year, spawn_min=spawn_min)


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
    return render_template("location.html", location=dict(loc), images=[dict(i) for i in images])


@app.route("/sweep")
def sweep():
    """Candidate review — auto-found spots to label."""
    db = get_db(app.config["DB_PATH"])
    candidates = db.execute(
        "SELECT * FROM candidates WHERE label IS NULL ORDER BY knn_score DESC"
    ).fetchall()
    return render_template("sweep.html", candidates=[dict(c) for c in candidates])


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
    """Serve S2 thumbnails — local disk first, fall back to R2 CDN."""
    monitor_path = app.config["IMAGE_DIR"] / filename
    sweep_path = Path("/Volumes/Z Slim/herring-spawn-data/candidates_sweep_2025") / filename
    if monitor_path.exists():
        return send_from_directory(str(app.config["IMAGE_DIR"]), filename)
    elif sweep_path.exists():
        return send_from_directory(str(sweep_path.parent), filename)
    # Fall back to R2 CDN (hardcoded free tier URL)
    base = app.config.get("IMAGE_BASE_URL", "") or "https://pub-85ee094121844d28a1597a25e41f5d15.r2.dev"
    if base:
        import requests
        r2_url = f"{base}/{filename}"
        try:
            resp = requests.get(r2_url, timeout=5)
            if resp.status_code == 200:
                return resp.content, 200, {"Content-Type": resp.headers.get("content-type", "image/png")}
        except Exception:
            pass
    return "Not found", 404


@app.route("/api/images/<int:image_id>/label", methods=["POST"])
def label_image(image_id):
    """Label a specific image as spawn/no-spawn/skip."""
    data = request.get_json()
    label = data.get("label")
    if label not in ("spawn", "no-spawn", "skip"):
        return jsonify({"error": "invalid label"}), 400
    db = get_db(app.config["DB_PATH"])
    db.execute("UPDATE images SET image_label=? WHERE id=?", (label, image_id))
    db.commit()
    return jsonify({"status": "ok"})


@app.route("/api/spawn-events")
def api_spawn_events():
    """Return all locations as GeoJSON for the map."""
    db = get_db(app.config["DB_PATH"])
    locs = db.execute("SELECT * FROM locations").fetchall()
    features = []
    for l in locs:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [l["lon"], l["lat"]]},
            "properties": {"id": l["id"], "name": l["name"], "region": l["region"]},
        })
    return jsonify({"type": "FeatureCollection", "features": features})


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
