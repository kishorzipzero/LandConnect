import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def isolated_output(tmp_path, monkeypatch):
    """Point results, database and audit trails at a temporary folder (real inputs stay read-only)."""
    from landconnect import config, db
    out = tmp_path / "output"
    out.mkdir()
    if (config.OUTPUT_DIR / "demo" / "result.json").exists():
        shutil.copytree(config.OUTPUT_DIR / "demo", out / "demo",
                        ignore=shutil.ignore_patterns("audit.jsonl", "decisions.json*"))
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(db, "DB_PATH", out / "test.db")
    return out
