"""STAC thumbnail downloader — optional, requires rasterio + pystac-client + pyproj."""

def fetch_latest_for_all(db, image_dir):
    """Check monitored locations for new S2 scenes. 
    Requires rasterio, pystac-client, planetary-computer, pyproj — 
    all optional since Render free tier can't install GDAL easily.
    """
    try:
        import numpy as np
        import rasterio
        from PIL import Image
        from pyproj import Transformer
        from pystac_client import Client as STACClient
        from rasterio.windows import Window
    except ImportError:
        return 0  # STAC deps not installed — skip

    # (rest of the fetcher logic stays here)
    return 0
