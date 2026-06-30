"""STAC thumbnail downloader — fetches latest S2 images for monitored locations."""

import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from pystac_client import Client as STACClient
from rasterio.windows import Window

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
WINDOW_PX = 512
MAX_CLOUD = 60
MAX_AGE_DAYS = 30
SEARCH_BUFFER = 0.06
SPAWN_SEASON_DEFAULT = ("02-01", "05-15")

_client = None


def get_catalog():
    global _client
    if _client is None:
        _client = STACClient.open(STAC_URL)
    return _client


def fetch_latest_for_all(db, image_dir: Path):
    """For each monitored location: check for the latest S2 scene and download if new."""
    locations = db.execute("SELECT * FROM locations").fetchall()
    added = 0
    catalog = get_catalog()

    for loc in locations:
        # Determine spawn season window
        season_start = loc["spawn_season_start"] or "02-01"
        season_end = loc["spawn_season_end"] or "05-15"
        year = datetime.now().year

        # Check last fetched date for this location
        last = db.execute(
            "SELECT scene_date FROM images WHERE location_id=? ORDER BY scene_date DESC LIMIT 1",
            (loc["id"],)
        ).fetchone()

        date_start = last["scene_date"] if last else f"{year}-{season_start}"
        date_end = f"{year}-{season_end}"

        scene = _find_best_scene(catalog, loc["lon"], loc["lat"], date_start, date_end)
        if scene is None:
            continue

        scene_date = scene.properties["datetime"][:10]
        if last and scene_date <= last["scene_date"]:
            continue  # No new scene

        # Download thumbnail
        fname = f"{loc['id']}_{scene_date}_{loc['lat']:.4f}_{loc['lon']:.4f}.png"
        img = _download_crop(scene, loc["lon"], loc["lat"])
        if img is None:
            continue

        image_dir.mkdir(parents=True, exist_ok=True)
        img.save(str(image_dir / fname), optimize=True)
        cloud = scene.properties.get("eo:cloud_cover", 100)

        db.execute(
            "INSERT INTO images (location_id, scene_id, scene_date, cloud_cover, filename) VALUES (?,?,?,?,?)",
            (loc["id"], scene.id, scene_date, cloud, fname)
        )
        db.commit()
        added += 1
        time.sleep(0.1)

    return added


def _find_best_scene(catalog, lon, lat, date_start, date_end):
    """Find the lowest-cloud S2 scene in the date range."""
    bbox = [lon - SEARCH_BUFFER, lat - SEARCH_BUFFER,
            lon + SEARCH_BUFFER, lat + SEARCH_BUFFER]
    try:
        search = catalog.search(
            collections=["sentinel-2-l2a"], bbox=bbox,
            datetime=f"{date_start}/{date_end}",
            query={"eo:cloud_cover": {"lte": MAX_CLOUD}}, max_items=10)
        items = list(search.items())
    except Exception:
        return None
    if not items:
        return None
    return min(items, key=lambda it: it.properties.get("eo:cloud_cover", 100))


def _download_crop(item, lon, lat):
    """Download 512x512 true-color thumbnail centered on (lon, lat)."""
    import planetary_computer as pc
    try:
        rh = pc.sign(item.assets["B04"].href)
        with rasterio.open(rh) as sr:
            crs, tr = sr.crs, sr.transform
            t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
            ux, uy = t.transform(lon, lat)
            col = int(round((~tr * (ux, uy))[0]))
            row = int(round((~tr * (ux, uy))[1]))
            hf = WINDOW_PX // 2
            h, w = sr.shape
            co = max(0, min(col - hf, w - WINDOW_PX))
            ro = max(0, min(row - hf, h - WINDOW_PX))
            win = Window(co, ro, WINDOW_PX, WINDOW_PX)
            gh = pc.sign(item.assets["B03"].href)
            bh = pc.sign(item.assets["B02"].href)
            red = sr.read(1, window=win) / 10000.0
            green = rasterio.open(gh).read(1, window=win) / 10000.0
            blue = rasterio.open(bh).read(1, window=win) / 10000.0
        def _stretch(b, lo=2, hi=98):
            d = b[b > 0]
            pl, ph = (np.percentile(d, (lo, hi)) if d.size else (0, 1))
            return np.clip((b - pl) / max(ph - pl, 0.001) * 255, 0, 255).astype(np.uint8)
        rgb = np.stack([_stretch(red * 255), _stretch(green * 255), _stretch(blue * 255)], axis=-1)
        return Image.fromarray(rgb)
    except Exception:
        return None
