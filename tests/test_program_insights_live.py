import dataclasses

from aws95.contracts.common import Result, Status
from aws95.contracts.insights import AccountInsight, InsightReport, InsightSection, ModelChoice
from aws95.programs.insights import make_insights
from aws95.providers.demo import demo_provider_set

A, B = "111111111111", "222222222222"
PAYLOAD = {"analysis": "insights", "accounts": [{"label": "Account 1", "resource_counts": {"ec2.instance": 4}},
                                                 {"label": "Account 2", "resource_counts": {}}]}


class Stub:
    def __init__(self, report=None):
        self.analyzed, self.report = [], report

    def preview_payload(self, scope, kind="insights", *, cancel=None):
        return Result.ok(dict(PAYLOAD, analysis=kind))

    def models(self, scope, account_id="", *, cancel=None):
        return Result.ok((ModelChoice("anthropic.m-2", "M2"), ModelChoice("anthropic.m-1", "M1")))

    def analyze(self, scope, kind="insights", payload=None, *, cancel=None):
        self.analyzed.append(payload)
        return Result.ok(self.report)


def make(program_env, qtbot, report=None):
    stub = Stub(report)
    ps = dataclasses.replace(demo_provider_set(), insights=stub, is_demo=False, is_live=True)
    w, ctx = program_env(make_insights, ps)
    from aws95.contracts.accounts import RosterEntry
    ctx.settings.set_roster((RosterEntry("demo-a", A), RosterEntry("demo-b", B)))   # program_env starts empty
    qtbot.addWidget(w)
    w.show()
    w.refresh()
    qtbot.waitUntil(lambda: w.consent_box.isEnabled() and len(w.model_pickers) == 2, timeout=3000)
    return w, ctx, stub


def test_estimate_and_pickers(program_env, qtbot):
    w, ctx, _ = make(program_env, qtbot)
    qtbot.waitUntil(lambda: len(w.model_pickers) == 2, timeout=3000)
    assert w.estimate_label.text().startswith("2 calls · about ") and "Bedrock charges per token" in w.estimate_label.text()
    w.refresh_models_button.click()
    qtbot.waitUntil(lambda: all(p.count() == 2 for p in w.model_pickers.values()), timeout=3000)
    w.model_pickers[A].setCurrentIndex(1)
    assert ctx.settings.insights_model(A) == "anthropic.m-1"


def test_analyze_needs_consent_and_a_model(program_env, qtbot):
    report = InsightReport("insights", "anthropic.m-2", (), (AccountInsight("Account 1", "anthropic.m-2",
                           (InsightSection("Summary", ("<b>bold</b> [link](http://x)",)),), 10, 2),
                           AccountInsight("Account 2", "", error="Choose a model for Account 2.")))
    w, ctx, stub = make(program_env, qtbot, report)
    assert not w.analyze_button.isEnabled()
    ctx.settings.set_insights_model(A, "anthropic.m-2")
    w.refresh_models_button.click()
    qtbot.waitUntil(lambda: w.model_pickers[A].count() == 2, timeout=3000)
    w.consent_box.setChecked(True)
    assert w.analyze_button.isEnabled()
    w.analyze()
    qtbot.waitUntil(lambda: "Summary" in w.results.toPlainText(), timeout=3000)
    assert stub.analyzed == [dict(PAYLOAD)]


def test_results_are_plain_text(program_env, qtbot):
    """Review Focus 5."""
    report = InsightReport("insights", "m", (), (AccountInsight("Account 1", "m",
                           (InsightSection("S", ("<b>bold</b> [link](http://x)",)),), 1, 1),))
    w, ctx, _ = make(program_env, qtbot, report)
    ctx.settings.set_insights_model(A, "m")
    w.consent_box.setChecked(True)
    w.analyze()
    qtbot.waitUntil(lambda: "<b>bold</b>" in w.results.toPlainText(), timeout=3000)
    assert w.results.toPlainText().count("<b>") == 1


def test_export_includes_payload_and_answers(program_env, qtbot, tmp_path, monkeypatch):
    report = InsightReport("insights", "m", (), (AccountInsight("Account 1", "m", (InsightSection("S", ("x",)),),
                                                                1, 1),))
    w, ctx, _ = make(program_env, qtbot, report)
    ctx.settings.set_insights_model(A, "m")
    w.consent_box.setChecked(True)
    w.analyze()
    qtbot.waitUntil(lambda: bool(w.results.toPlainText()), timeout=3000)
    out = tmp_path / "i.md"
    monkeypatch.setattr(w, "ask_save_path", lambda *a, **k: str(out))
    assert w.export()
    text = out.read_text()
    assert "## Payload sent" in text and '"Account 1"' in text and "## Account 1" in text
