"""Tamper-evident audit trail (hash chain).

Every harmonisation decision - which file was read (with its SHA-256), which CRS transform was
applied, which records were linked, which source won a conflict and why, every reviewer decision -
is appended to output/<ws>/audit.jsonl. Each entry stores the SHA-256 of the previous entry, so
changing or deleting any past entry breaks the chain from that point on (`verify`). Publishing the
latest head hash (e.g. in a gazette notice or an external timestamping service) anchors the whole
history - the same idea as a blockchain ledger, without needing a network of nodes.

    python -m landconnect.audit demo            # verify the demo workspace's chain
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

GENESIS = "0" * 64


def _digest(entry):
    body = {k: entry[k] for k in ("seq", "ts", "kind", "subject", "actor", "detail", "prev")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seq, self.head = 0, GENESIS
        if self.path.exists():
            last = None
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = line
            if last:
                e = json.loads(last)
                self.seq, self.head = e["seq"], e["hash"]
        self._fh = None

    def append(self, kind, subject=None, detail=None, actor="system"):
        self.seq += 1
        e = {"seq": self.seq, "ts": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind,
             "subject": subject, "actor": actor, "detail": detail or {}, "prev": self.head}
        e["hash"] = _digest(e)
        self.head = e["hash"]
        if self._fh is None:
            self._fh = open(self.path, "a", encoding="utf-8")
        self._fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        return e

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None


def append_one(path, kind, subject=None, detail=None, actor="system"):
    log = AuditLog(path)
    try:
        return log.append(kind, subject, detail, actor)
    finally:
        log.close()


def verify(path):
    path = Path(path)
    if not path.exists():
        return {"ok": True, "entries": 0, "head": GENESIS, "broken_at": None}
    prev, n = GENESIS, 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            n += 1
            if e.get("prev") != prev or _digest(e) != e.get("hash") or e.get("seq") != n:
                return {"ok": False, "entries": n, "head": prev, "broken_at": e.get("seq", n)}
            prev = e["hash"]
    return {"ok": True, "entries": n, "head": prev, "broken_at": None}


def read(path, subject=None, kind=None, limit=200):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            if subject and e.get("subject") != subject and subject not in json.dumps(e.get("detail"), ensure_ascii=False):
                continue
            if kind and not e["kind"].startswith(kind):
                continue
            out.append(e)
    return out[-limit:][::-1]


if __name__ == "__main__":
    from . import config
    ws = sys.argv[1] if len(sys.argv) > 1 else config.DEFAULT_WS
    r = verify(config.ws_paths(ws)["audit"])
    print(("CHAIN INTACT" if r["ok"] else f"CHAIN BROKEN at entry {r['broken_at']}") +
          f" - {r['entries']} entries, head {r['head'][:16]}...")
