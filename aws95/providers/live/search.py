"""The live resource matcher for ComposedSearch (2d spec §4.1): one indexed SQL query over each account's latest
snapshot instead of loading every resource for every keystroke."""
from __future__ import annotations

from aws95.contracts.common import Scope
from aws95.contracts.search import SearchHit
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.composed import PER_KIND, resource_hit
from aws95.providers.live.common import Roster, latest_snapshots, marks, scope_accounts
from aws95.store import Store


def like_pattern(text: str) -> str:
    """A LIKE pattern that matches `text` literally anywhere (ESCAPE '\\')."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def store_resource_hits(store: Store, roster: Roster, scope: Scope, text: str,
                        cancel: CancelToken | None = None) -> list[SearchHit]:
    raise_if_cancelled(cancel)
    accounts = scope_accounts(scope, roster())
    if not accounts:
        return []
    with store.reader() as db:
        latest = latest_snapshots(db, accounts)
        if not latest:
            return []
        ids = [r["id"] for r in latest.values()]
        rows = db.execute(f"SELECT s.account, r.rtype, r.region, r.rid FROM resource_state r "
                          f"JOIN snapshots s ON s.id = r.snapshot_id WHERE r.snapshot_id IN ({marks(len(ids))}) "
                          f"AND r.rid LIKE ? ESCAPE '\\' ORDER BY r.rid, s.account LIMIT ?",
                          [*ids, like_pattern(text), PER_KIND]).fetchall()
    return [resource_hit(r["account"], r["rtype"], r["region"], r["rid"]) for r in rows]
