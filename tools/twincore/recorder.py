"""Spec 004 twin-core: append-only JSONL recorder (driver-runtime.md §1).

One file per record kind under the match directory
(`output/twincore-recordings/<match>/<kind>.jsonl`); every line carries the
envelope {match_id, tick, wall_ts} (wall_ts observability-only — no gameplay
decision may read it, contract/twin-core-protocol.md §6). In-memory ring for
event_raw only. Replay: scan() with kind/tick filters (SC-007).
"""
import itertools
import json
import os
import threading
import time

KINDS = (
    "event_raw", "classification", "delta", "rollup", "digest", "packet",
    "packet_rejected", "revision", "decision", "command", "validation",
    "usage", "breach", "degradation", "question",
)

RING_CAP = 512


def _kind_file(name):
    return "%s.jsonl" % name


def scan(match_dir, kinds=None, tick_range=None):
    """Replay a recorded match segment: kind/tick filters over all kind files.

    Returns records sorted by tick (SC-007 reconstructable, offline).
    """
    if kinds is None:
        kinds = list(KINDS)
    out = []
    for kind in kinds:
        path = os.path.join(match_dir, _kind_file(kind))
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if tick_range is not None:
                    lo, hi = tick_range
                    if not (lo <= rec["tick"] <= hi):
                        continue
                out.append(rec)
    return sorted(out, key=lambda r: (r["tick"], r.get("_seq", 0)))


class Recorder:
    """Append-only match artefact store (FR-021)."""

    def __init__(self, match_dir, match_id, ring_cap=RING_CAP):
        self.match_dir = match_dir
        self.match_id = match_id
        self.ring_cap = ring_cap
        self._lock = threading.Lock()
        self._ring = []
        self._seq = itertools.count()
        os.makedirs(match_dir, exist_ok=True)

    def record(self, kind, tick=None, **payload):
        """Append one record of `kind`; event_raw also enters the ring."""
        if kind not in KINDS:
            raise ValueError("unknown recorder kind: %r" % (kind,))
        line = {
            "match_id": self.match_id,
            "tick": 0 if tick is None else tick,
            "wall_ts": time.time(),
            "_seq": next(self._seq),
        }
        line.update(payload)
        with self._lock:
            with open(os.path.join(self.match_dir, _kind_file(kind)),
                      "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
            if kind == "event_raw":
                self._ring.append(payload)
                if len(self._ring) > self.ring_cap:
                    del self._ring[:len(self._ring) - self.ring_cap]
        return line

    def ring(self):
        """Newest event_raw records (bounded, D5)."""
        return list(self._ring)

    def close(self):
        pass


def replay(match_dir, kinds=None, tick_range=None):
    """Alias kept for contract §6 replay wording."""
    return scan(match_dir, kinds=kinds, tick_range=tick_range)