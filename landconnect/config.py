"""Project-wide settings: paths, workspaces, target CRS, thresholds and scoring weights."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TRAIN_DIR = DATA_DIR / "_training"    # separate synthetic wards used to train the matcher
OUTPUT_DIR = ROOT / "output"
MODEL_DIR = OUTPUT_DIR / "models"
WEB_DIR = ROOT / "web"

# A workspace is one area being harmonised: data/<ws>/raw (inputs), data/<ws>/_truth (optional hidden
# truth, used only for evaluation), data/<ws>/workspace.json (title, notes). Results go to output/<ws>/.
DEFAULT_WS = "demo"


def ws_paths(ws):
    out = OUTPUT_DIR / ws
    return {"root": DATA_DIR / ws, "raw": DATA_DIR / ws / "raw", "truth": DATA_DIR / ws / "_truth" / "truth.json",
            "meta": DATA_DIR / ws / "workspace.json", "out": out, "web": out / "web",
            "result": out / "result.json", "decisions": out / "decisions.json", "audit": out / "audit.jsonl"}


def list_workspaces():
    return sorted(p.name for p in DATA_DIR.iterdir() if (p / "raw").is_dir() and not p.name.startswith("_"))


# Working CRS. Every run switches this to the UTM zone that contains the data (see utm_epsg_for);
# 32643 = WGS 84 / UTM zone 43N (western India) is only the default.
TARGET_EPSG = 32643
WGS84 = 4326


def utm_epsg_for(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def set_target_epsg(epsg):
    global TARGET_EPSG
    TARGET_EPSG = int(epsg)


# Synthetic demo ward is placed in Mangaluru (close to the zone 43 central meridian).
WARD_ORIGIN_LONLAT = (74.8560, 12.8700)
WARD_NAME = "Ward 47 (synthetic demo)"

ORTHO_RES = 0.25   # metres per pixel of the drone orthophoto
DSM_RES = 0.5      # metres per pixel of the DSM / DTM

# Confidence scoring
AUTO_APPROVE = 85
FLAG = 70
WEIGHTS = {"G": 0.30, "A": 0.20, "S": 0.20, "M": 0.15, "T": 0.15}

# Reliability of each source when sources disagree on geometry (1 = most trusted)
SOURCE_RELIABILITY = {
    "gnss": 1.00,
    "ground_truth": 0.95,
    "drone_ai": 0.80,
    "municipal": 0.65,
    "cadastral": 0.60,
}

# Owner-name similarity (0-100) below which two records are treated as different people
OWNER_MATCH_THRESHOLD = 75

# Residual misfit (m) against GNSS control above which a map in a declared CRS is re-fitted
# (legacy datums such as Kalianpur 1975 carry metre-level shifts after reprojection)
DATUM_REFIT_RMSE_M = 1.0

# Optional: Google Map Tiles API key for a Google satellite basemap (display only).
# Set the environment variable GOOGLE_MAPS_API_KEY; never hard-code it.
