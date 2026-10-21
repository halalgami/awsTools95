import json

import pytest
from PySide6.QtCore import QSettings

from aws95.contracts.common import Scope, Status
from aws95.core.settings import Settings
from aws95.providers.live import live_provider_set
from tests.store_seed import DEV, PINNED, PROD, ROSTER, seed_store


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = seed_store(tmp_path / "aws95.db")
    settings = Settings(QSettings(str(tmp_path / "i.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    sent = []

    def fake_converse(session, region, model_id, system_prompt, entry, *, cancel=None):
        sent.append((region, model_id, entry))
        if model_id == "broken":
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Converse")
        return {"text": "## Summary\n- Fine.", "input_tokens": 100, "output_tokens": 20}
    from aws95.aws.insights import bedrock
    monkeypatch.setattr(bedrock, "converse", fake_converse)
    monkeypatch.setattr(bedrock, "list_models", lambda session, region, cancel=None: [("anthropic.m-2", "M2")])
    ps = live_provider_set(settings, store=store, clock=lambda: PINNED, session_factory=lambda profile: object())
    yield ps, settings, sent
    store.close()


def test_preview_is_per_account_and_clean(env):
    ps, _, _ = env
    r = ps.insights.preview_payload(Scope(), "insights")
    assert r.status is Status.OK
    accounts = r.data["accounts"]
    assert [a["label"] for a in accounts] == ["Account 1", "Account 2"]
    text = json.dumps(r.data)
    assert DEV not in text and PROD not in text and "example-logs" not in text and "alice" not in text
    assert accounts[0]["open_findings_by_rule"] and accounts[0]["resource_counts"]


def test_models_listed_per_account(env):
    ps, _, _ = env
    r = ps.insights.models(Scope(), DEV)
    assert r.status is Status.OK and r.data[0].model_id == "anthropic.m-2"


def test_analyze_sends_exactly_the_reviewed_entries(env):
    ps, settings, sent = env
    settings.set_insights_model(DEV, "anthropic.m-2")
    settings.set_insights_model(PROD, "anthropic.m-2")
    payload = ps.insights.preview_payload(Scope(), "oddities").data
    r = ps.insights.analyze(Scope(), "oddities", payload)
    assert r.status is Status.OK and [e for _, _, e in sent] == payload["accounts"]
    assert [a.label for a in r.data.accounts] == ["Account 1", "Account 2"]
    assert r.data.accounts[0].sections[0].title == "Summary" and r.data.accounts[0].input_tokens == 100


def test_account_without_model_is_skipped(env):
    ps, settings, sent = env
    settings.set_insights_model(DEV, "anthropic.m-2")
    payload = ps.insights.preview_payload(Scope(), "insights").data
    r = ps.insights.analyze(Scope(), "insights", payload)
    assert r.status is Status.PARTIAL and len(sent) == 1
    assert "Choose a model" in r.data.accounts[1].error


def test_one_account_fails_other_answers(env):
    """Review Focus 3."""
    ps, settings, sent = env
    settings.set_insights_model(DEV, "anthropic.m-2")
    settings.set_insights_model(PROD, "broken")
    payload = ps.insights.preview_payload(Scope(), "insights").data
    r = ps.insights.analyze(Scope(), "insights", payload)
    assert r.status is Status.PARTIAL and r.data.accounts[0].sections and "Model access" in r.data.accounts[1].error


def test_analyze_refuses_when_accounts_changed(env):
    """Review Focus 2."""
    ps, settings, sent = env
    settings.set_insights_model(DEV, "anthropic.m-2")
    payload = ps.insights.preview_payload(Scope(), "insights").data
    settings.set_roster(ROSTER[:1])
    r = ps.insights.analyze(Scope(), "insights", payload)
    assert r.status is Status.ERROR and "account list changed" in r.message and sent == []


def test_analyze_refuses_an_unreviewed_payload(env):
    ps, settings, sent = env
    assert ps.insights.analyze(Scope(), "insights", None).status is Status.ERROR and sent == []


def test_settings_round_trip(tmp_path):
    s = Settings(QSettings(str(tmp_path / "x.ini"), QSettings.Format.IniFormat))
    assert s.insights_model(DEV) is None
    s.set_insights_model(DEV, "anthropic.m-2")
    assert s.insights_model(DEV) == "anthropic.m-2"
