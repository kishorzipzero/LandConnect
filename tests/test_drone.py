"""Uploaded drone orthomosaics (RGB GeoTIFFs) become the Drone map type in any area."""
import numpy as np
from pyproj import Transformer

from landconnect import geotiff, ingest, pipeline


def _ortho(path, x0, y_top, res=0.05, size=(400, 300), shade=120):
    img = np.full((size[1], size[0], 3), shade, np.uint8)
    img[:, : size[0] // 2, 0] = 200                     # a visible left half, so the warp is not a blank image
    geotiff.write(path, img, {"x0": x0, "y_top": y_top, "res": res}, 32643)


def test_uploaded_orthos_publish_as_drone_map(tmp_path):
    raw, web = tmp_path / "raw", tmp_path / "web"
    raw.mkdir(); web.mkdir()
    x0, y_top = 777000.0, 1435000.0                      # UTM 43N, near Bengaluru
    _ortho(raw / "ortho_2024.tif", x0, y_top, shade=90)
    _ortho(raw / "ortho_2026.tif", x0, y_top, shade=160)
    ds = [ingest.load_dataset(p) for p in sorted(raw.iterdir())]
    assert [d.role for d in ds] == ["ortho", "ortho"]
    ctx = {"datasets": ds, "paths": {"web": web}, "url_prefix": "/output/x/web", "rasters": {}, "log": []}
    pipeline._publish_uploaded_orthos(ctx)
    r = ctx["rasters"]
    assert set(r) == {"ortho_t1", "ortho_t2"}
    assert (r["ortho_t1"]["date"], r["ortho_t2"]["date"]) == ("2024", "2026")
    assert (web / "ortho_t2.jpg").stat().st_size > 1000
    lon, lat = Transformer.from_crs(32643, 4326, always_xy=True).transform(x0 + 10, y_top - 7.5)   # image centre
    (s, w), (n, e) = r["ortho_t2"]["bounds"]
    assert s < lat < n and w < lon < e
    assert (n - s) * 111_000 < 20                       # 15 m tall image, not something wildly off


def test_bad_ortho_is_logged_not_fatal(tmp_path):
    raw, web = tmp_path / "raw", tmp_path / "web"
    raw.mkdir(); web.mkdir()
    _ortho(raw / "ortho.tif", 777000.0, 1435000.0)
    d = ingest.load_dataset(raw / "ortho.tif")
    (raw / "ortho.tif").write_bytes(b"not a tiff any more")
    ctx = {"datasets": [d], "paths": {"web": web}, "url_prefix": "/x", "rasters": {}, "log": []}
    pipeline._publish_uploaded_orthos(ctx)
    assert ctx["rasters"] == {} and "could not be read" in ctx["log"][-1]
