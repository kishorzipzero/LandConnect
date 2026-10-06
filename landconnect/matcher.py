"""AI/ML-based spatial matching.

Decides whether a feature in one dataset is the same real-world object as a feature in
another (e.g. cadastral parcel <-> municipal property polygon). For each candidate pair it
computes geometric and attribute features and a gradient-boosted classifier predicts the
match probability. The model is trained on separate synthetic training wards (never on the
ward being evaluated), and a merged polygon that matches several parcels is detected
afterwards.
"""
import pickle

import numpy as np
import shapely
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import cross_val_score
from shapely.strtree import STRtree

from . import config
from .schema_mapper import id_similarity

FEATURES = ["iou", "cover_a", "cover_b", "centroid_dist_m", "area_ratio", "hausdorff_m", "id_similarity"]
MODEL_PATH = config.MODEL_DIR / "matcher_v3.pkl"
# training wards span the positional quality seen in real ULB layers: (seed, vertex noise m, shift m)
TRAIN_WARDS = [(11, 0.3, (0.3, -0.2)), (12, 0.6, (1.0, 0.5)), (13, 1.0, (-1.5, 1.2)), (14, 1.5, (2.0, -1.5)),
               (15, 2.0, (-2.5, -2.0)), (16, 0.8, (0.0, 3.0))]


def pair_features(a, b, a_id, b_id):
    inter = a.intersection(b).area
    union = a.union(b).area or 1e-9
    id_sim = id_similarity(a_id, b_id)
    return [inter / union, inter / (a.area or 1e-9), inter / (b.area or 1e-9),
            a.centroid.distance(b.centroid), min(a.area, b.area) / (max(a.area, b.area) or 1e-9),
            shapely.hausdorff_distance(a, b), id_sim]


def candidate_pairs(parcels, others, search_m=2.0):
    """Yield (i, j, features) for every parcel / other-feature pair that is spatially plausible."""
    geoms = [o["geom"] for o in others]
    tree = STRtree(geoms)
    out = []
    for i, p in enumerate(parcels):
        for j in tree.query(p["geom"].buffer(search_m)):
            j = int(j)
            if not p["geom"].buffer(search_m).intersects(geoms[j]):
                continue
            out.append((i, j, pair_features(p["geom"], geoms[j], p["plot_id"], others[j].get("plot_id"))))
    return out


def _training_rows(seed, noise, shift):
    from . import pipeline, synth  # local import: training reuses the real pipeline steps
    root = config.TRAIN_DIR / f"ward_{seed}"
    truth = synth.generate(root, seed=seed, rasters=False, muni_noise=noise, muni_shift=shift, departments=False)
    ctx = pipeline.prepare_vectors(root / "raw")
    parcels, muni = ctx["parcels"], ctx["municipal"]
    mm = truth["municipal_map"]
    X, y = [], []
    for i, j, f in candidate_pairs(parcels, muni):
        X.append(f)
        y.append(1 if parcels[i]["plot_id"] in mm.get(muni[j]["record_id"], []) else 0)
    return X, y


def train():
    X, y = [], []
    for s, noise, shift in TRAIN_WARDS:
        xs, ys = _training_rows(s, noise, shift)
        X += xs
        y += ys
    X, y = np.asarray(X), np.asarray(y)
    model = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.1, random_state=0)
    auc = cross_val_score(model, X, y, cv=4, scoring="roc_auc")
    f1 = cross_val_score(model, X, y, cv=4, scoring="f1")
    model.fit(X, y)
    info = {"model": "GradientBoostingClassifier (150 trees, depth 3)", "training_wards": len(TRAIN_WARDS),
            "training_pairs": int(len(y)), "positive_pairs": int(y.sum()),
            "cv_roc_auc": round(float(auc.mean()), 4), "cv_f1": round(float(f1.mean()), 4),
            "feature_importance": {k: round(float(v), 3) for k, v in zip(FEATURES, model.feature_importances_)}}
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as fh:
        pickle.dump({"model": model, "info": info, "features": FEATURES}, fh)
    return model, info


def load_or_train():
    if MODEL_PATH.exists():
        with open(MODEL_PATH, "rb") as fh:
            d = pickle.load(fh)
        if d.get("features") == FEATURES:
            return d["model"], d["info"]
    return train()


def match(model, parcels, others, threshold=0.5):
    """Returns (links, merged): links[i] = (j, prob) best match per parcel; merged = {j: [i, ...]}."""
    pairs = candidate_pairs(parcels, others)
    if not pairs:
        return {}, {}
    probs = model.predict_proba(np.asarray([f for _, _, f in pairs]))[:, 1]
    links, by_other = {}, {}
    for (i, j, f), pr in zip(pairs, probs):
        if pr < threshold:
            continue
        by_other.setdefault(j, []).append(i)
        if i not in links or pr > links[i][1]:
            links[i] = (j, float(pr), dict(zip(FEATURES, [round(float(v), 4) for v in f])))
    # a parcel keeps only its best partner; an "other" polygon linked to 2+ parcels is a merge
    merged = {}
    for j, members in by_other.items():
        members = [i for i in members if links.get(i, (None,))[0] == j]
        if len(members) > 1:
            merged[j] = members
    return links, merged
