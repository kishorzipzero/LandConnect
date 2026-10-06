"""Minimal GeoTIFF read / write (single-band elevation rasters and RGB orthophotos) without GDAL.

Only the three GeoTIFF tags needed for a north-up raster in a projected or geographic CRS are used:
  33550 ModelPixelScale  (res_x, res_y, 0)
  33922 ModelTiepoint    (0, 0, 0, x_left, y_top, 0)      pixel (0, 0) -> map coordinates of its corner
  34735 GeoKeyDirectory  model type, raster type (PixelIsArea) and the EPSG code
Files written here open in QGIS / GDAL; files from other software are read if they follow the same layout.
"""
import numpy as np
from PIL import Image, TiffImagePlugin

PIXEL_SCALE, TIEPOINT, GEOKEYS = 33550, 33922, 34735


def write(path, array, grid, epsg):
    """array: 2-D float32 (elevation) or HxWx3 uint8 (image). grid: {x0, y_top, res}."""
    a = np.asarray(array)
    img = Image.fromarray(a.astype(np.float32), mode="F") if a.ndim == 2 else Image.fromarray(a.astype(np.uint8), "RGB")
    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    ifd.tagtype[PIXEL_SCALE] = ifd.tagtype[TIEPOINT] = 12          # DOUBLE
    ifd[PIXEL_SCALE] = (float(grid["res"]), float(grid["res"]), 0.0)
    ifd[TIEPOINT] = (0.0, 0.0, 0.0, float(grid["x0"]), float(grid["y_top"]), 0.0)
    projected = epsg != 4326
    ifd.tagtype[GEOKEYS] = 3                                          # SHORT
    ifd[GEOKEYS] = (1, 1, 0, 3,
                    1024, 0, 1, 1 if projected else 2,                # GTModelType: projected / geographic
                    1025, 0, 1, 1,                                    # GTRasterType: PixelIsArea
                    3072 if projected else 2048, 0, 1, int(epsg))     # ProjectedCSType / GeographicType
    img.save(path, tiffinfo=ifd, compression="tiff_deflate")


def read(path):
    """-> (array, {"x0", "y_top", "res", "width", "height"}, epsg or None). Raises ValueError if not geo-referenced."""
    with Image.open(path) as img:
        tags = img.tag_v2
        if PIXEL_SCALE not in tags or TIEPOINT not in tags:
            raise ValueError("TIFF has no GeoTIFF georeferencing tags")
        sx, sy = tags[PIXEL_SCALE][:2]
        tp = tags[TIEPOINT]
        i, j, x, y = tp[0], tp[1], tp[3], tp[4]
        keys = list(tags.get(GEOKEYS) or [])
        epsg = None
        for k in range(4, len(keys) - 3, 4):
            if keys[k] in (3072, 2048) and keys[k + 1] == 0:
                epsg = int(keys[k + 3])
        arr = np.array(img)
    grid = {"x0": float(x - i * sx), "y_top": float(y + j * sy), "res": float(sx),
            "width": int(arr.shape[1]), "height": int(arr.shape[0])}
    if abs(sx - sy) > 1e-9 * max(sx, 1):
        grid["res_y"] = float(sy)
    return arr, grid, epsg


def info(path):
    """Header summary for the Data tab: size, bands, resolution, CRS, and whether it looks like elevation."""
    arr, grid, epsg = read(path)
    bands = 1 if arr.ndim == 2 else arr.shape[2]
    kind = "elevation" if bands == 1 and arr.dtype.kind == "f" else "image"
    return {"width": grid["width"], "height": grid["height"], "bands": bands, "dtype": str(arr.dtype),
            "res": grid["res"], "epsg": epsg, "kind": kind, "x0": grid["x0"], "y_top": grid["y_top"]}
