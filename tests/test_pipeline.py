"""End-to-end: a small synthetic ward through every pipeline step, scored against its truth."""
from landconnect import evaluate, pipeline, synth


def test_small_ward_end_to_end(tmp_path):
    truth = synth.generate(tmp_path / "ward", seed=5, rasters=True, n_cols=3, n_rows=2)
    ctx = pipeline.new_ctx(tmp_path / "ward" / "raw")
    for step in ("ingest", "schema", "crs", "extract", "topology", "match", "change", "conflict", "confidence"):
        pipeline.STEP_FUNCS[step](ctx)
    ev = evaluate.run(ctx, truth)

    assert ctx["topology"]["overlaps_remaining"] == 0 and ctx["topology"]["invalid_remaining"] == 0
    assert ev["schema_mapping"]["accuracy"] == 1.0
    assert ev["positional"]["Fused output (GNSS + topology)"]["mean_m"] < 0.3
    assert ev["matching"]["f1"] > 0.95
    assert ev["extraction"]["f1"] > 0.95
    assert ev["issues"]["Stale owner in RoR or municipal record"]["recall"] == 1.0
    assert ev["issues"]["Encroaching building"]["recall"] == 1.0
    approved = ev["calibration"].get("approved", {"error_rate": 0})
    assert approved["error_rate"] <= 0.02                       # the score must be honest
    assert all(p["status"] in ("approved", "flagged", "review") for p in ctx["parcels"])
