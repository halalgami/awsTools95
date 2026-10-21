# Upstream: labsnap/labsnap/store/schema.py @ 1b1d437 and ctwatch/ctwatch/store/cache.py @ 254ff31 — adapted for aws95; owned here from now on.
"""Schema of aws95.db (spec §3.3), v1 plus v2 (snapshot sections, resumable-snapshots spec §4). One version for both engines.

Money and metric values are TEXT holding the exact decimal string; never REAL. Times are UTC "YYYY-MM-DDTHH:MM:SSZ".
"""
from __future__ import annotations

SCHEMA_VERSION = 3

_V1 = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE snapshots(
  id           INTEGER PRIMARY KEY,
  account      TEXT NOT NULL,
  caller_arn   TEXT NOT NULL,
  taken_at     TEXT NOT NULL,
  tool_version TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('partial', 'complete')),
  regions      TEXT NOT NULL DEFAULT '[]');
CREATE INDEX ix_snap_account ON snapshots(account, taken_at);

CREATE TABLE collector_runs(
  snapshot_id       INTEGER NOT NULL REFERENCES snapshots(id),
  collector         TEXT NOT NULL,
  status            TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
  error             TEXT,
  duration_ms       INTEGER,
  regions_requested TEXT NOT NULL,
  regions_covered   TEXT NOT NULL,
  denials           INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(snapshot_id, collector));

CREATE TABLE resource_state(
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  collector   TEXT NOT NULL,
  rtype       TEXT NOT NULL,
  region      TEXT NOT NULL,
  rid         TEXT NOT NULL,
  attrs       TEXT NOT NULL,
  partial     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(snapshot_id, rtype, region, rid));
CREATE INDEX ix_rs_identity ON resource_state(rtype, region, rid);

CREATE TABLE metrics(
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  collector   TEXT NOT NULL,
  name        TEXT NOT NULL,
  dims        TEXT NOT NULL DEFAULT '{}',
  period      TEXT NOT NULL DEFAULT '',     -- '' = no period (a point-in-time metric); never NULL inside the key
  value       TEXT NOT NULL,
  unit        TEXT,
  observed_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, name, dims, period));
CREATE INDEX ix_metrics_series ON metrics(name, period, observed_at);

CREATE TABLE findings(
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  rule        TEXT NOT NULL,
  rtype       TEXT NOT NULL,
  region      TEXT NOT NULL,
  rid         TEXT NOT NULL,
  severity    TEXT NOT NULL,
  detail      TEXT,
  PRIMARY KEY(snapshot_id, rule, rtype, region, rid));
CREATE INDEX ix_findings_rule ON findings(rule, rid);

CREATE TABLE mutes(
  id         INTEGER PRIMARY KEY,
  account    TEXT,
  rule       TEXT NOT NULL,
  rid        TEXT,
  reason     TEXT NOT NULL,
  muted_at   TEXT NOT NULL,
  expires_at TEXT);

CREATE TABLE seeded_findings(
  account    TEXT NOT NULL,
  rule       TEXT NOT NULL,
  rid        TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  source     TEXT NOT NULL,
  PRIMARY KEY(account, rule, rid));

CREATE TABLE events(
  event_id       TEXT PRIMARY KEY,
  account        TEXT NOT NULL,
  region         TEXT NOT NULL,
  event_time_utc TEXT NOT NULL,
  principal_type TEXT NOT NULL,
  principal_name TEXT NOT NULL,
  is_human       INTEGER NOT NULL,
  event_source   TEXT NOT NULL,
  event_name     TEXT NOT NULL,
  read_only      INTEGER NOT NULL,
  error_code     TEXT,
  source_ip      TEXT,
  user_agent     TEXT,
  resource_arns  TEXT NOT NULL DEFAULT '[]',
  risk_level     INTEGER NOT NULL DEFAULT 0,
  risk_rule      TEXT,
  origin         TEXT NOT NULL CHECK (origin IN ('lookup', 'import')),
  raw            BLOB NOT NULL);
CREATE INDEX ix_ev_account_time ON events(account, event_time_utc);
CREATE INDEX ix_ev_principal ON events(principal_name);
CREATE INDEX ix_ev_source ON events(event_source);
CREATE INDEX ix_ev_name ON events(event_name);
CREATE INDEX ix_ev_risk ON events(risk_level);

CREATE TABLE activity_coverage(
  account    TEXT NOT NULL,
  region     TEXT NOT NULL,
  kind       TEXT NOT NULL,
  range_from TEXT NOT NULL,
  range_to   TEXT NOT NULL,
  PRIMARY KEY(account, region, kind, range_from));
"""

_V2 = """
CREATE TABLE snapshot_sections(
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  collector   TEXT NOT NULL,
  region      TEXT NOT NULL,
  state       TEXT NOT NULL CHECK (state IN ('waiting','running','done','partial','denied',
                                             'timed_out','failed','stopped')),
  started_at  TEXT,
  ended_at    TEXT,
  rows        INTEGER NOT NULL DEFAULT 0,
  denials     INTEGER NOT NULL DEFAULT 0,
  error       TEXT,
  PRIMARY KEY(snapshot_id, collector, region));

-- Sections for snapshots taken before v2: one per requested region, done when it was covered (resumable-snapshots
-- spec §4.1). Denials were kept per collector in v1, so sections start at 0.
INSERT INTO snapshot_sections(snapshot_id, collector, region, state, rows, denials, error)
SELECT r.snapshot_id, r.collector, req.value,
       CASE WHEN EXISTS (SELECT 1 FROM json_each(r.regions_covered) c WHERE c.value = req.value)
            THEN 'done' ELSE 'failed' END,
       (SELECT COUNT(*) FROM resource_state s
         WHERE s.snapshot_id = r.snapshot_id AND s.collector = r.collector AND s.region = req.value),
       0,
       CASE WHEN EXISTS (SELECT 1 FROM json_each(r.regions_covered) c WHERE c.value = req.value)
            THEN NULL ELSE r.error END
FROM collector_runs r, json_each(r.regions_requested) req;
"""

_V3 = """
CREATE TABLE rule_overrides(
  rule       TEXT PRIMARY KEY,
  disabled   INTEGER NOT NULL DEFAULT 0,
  severity   TEXT CHECK (severity IS NULL OR severity IN ('critical','high','medium','low')),
  params     TEXT,
  changed_at TEXT NOT NULL);
"""

# Ordered: MIGRATIONS[i] moves a database from user_version i to i + 1.
MIGRATIONS: tuple[str, ...] = (_V1, _V2, _V3)
