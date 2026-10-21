# Upstream: labsnap/labsnap/rules/view.py @ 1b1d437 — adapted for aws95; owned here from now on.
"""A read-only window onto one stored snapshot. Rules receive this and nothing else: they never call AWS, which is
what makes them replayable. The rows are read once, through a read-only connection."""
from __future__ import annotations

import json
from decimal import Decimal

from aws95.aws.engines.labsnap.model import Metric, Resource


class SnapshotView:
    def __init__(self, store, snapshot_id: int) -> None:
        with store.reader() as db:
            row = db.execute("SELECT account, taken_at FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if row is None:
                raise ValueError(f"no snapshot with id {snapshot_id}")
            res = db.execute("SELECT rtype, region, rid, attrs, partial FROM resource_state WHERE snapshot_id=?",
                             (snapshot_id,)).fetchall()
            met = db.execute("SELECT name, value, dims, period, unit FROM metrics WHERE snapshot_id=?",
                             (snapshot_id,)).fetchall()
        self._snapshot_id = snapshot_id
        self._account = row["account"]
        self._taken_at = row["taken_at"]
        self._resources = tuple(Resource(r["rtype"], r["region"], r["rid"], json.loads(r["attrs"]), bool(r["partial"]))
                                for r in res)
        self._metrics = tuple(Metric(m["name"], Decimal(m["value"]), json.loads(m["dims"]), m["period"] or None, m["unit"])
                              for m in met)

    @property
    def snapshot_id(self) -> int:
        return self._snapshot_id

    @property
    def account(self) -> str:
        return self._account

    @property
    def taken_at(self) -> str:
        return self._taken_at

    def resources(self, rtype: str | None = None) -> list[Resource]:
        return [r for r in self._resources if rtype is None or r.rtype == rtype]

    def metrics(self, name: str | None = None) -> list[Metric]:
        return [m for m in self._metrics if name is None or m.name == name]
