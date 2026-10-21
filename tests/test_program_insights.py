import dataclasses
import re
import threading

import pytest
from PySide6.QtWidgets import QLabel

from aws95.contracts.common import Result
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.insights import make_insights
from aws95.providers.demo import demo_provider_set


class Spy:
    """Wraps the insights provider: records calls; analyze can be held until released."""

    def __init__(self, inner, hold: bool = False, fail: bool = False):
        self.inner, self.calls, self.payloads, self.fail = inner, [], [], fail
        self.gate = threading.Event()
        self.finished = threading.Event()
        if not hold:
            self.gate.set()

    def preview_payload(self, scope, kind="insights", *, cancel=None):
        return self.inner.preview_payload(scope, kind, cancel=cancel)

    def analyze(self, scope, kind="insights", payload=None, *, cancel=None):
        self.calls.append((scope, kind))
        self.payloads.append(payload)
        self.gate.wait(5)
        try:
            if self.fail:
                raise RuntimeError("boom")
            return self.inner.analyze(scope, kind, payload, cancel=cancel)
        finally:
            self.finished.set()


def _make(program_env, qtbot, spy=None):
    ps = demo_provider_set()
    spy = spy or Spy(ps.insights)
    w, ctx = program_env(make_insights, dataclasses.replace(ps, insights=spy))
    qtbot.addWidget(w)
    w.show()
    # StateView starts in CONTENT: wait for render evidence, not the state
    qtbot.waitUntil(lambda: '"analysis": "insights"' in w.preview.toPlainText())
    return w, spy


@pytest.fixture
def demo(program_env, qtbot):
    return _make(program_env, qtbot)


def _letter(text):
    m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
    return m.group(1).lower() if m else None


def test_empty_mode_cannot_analyze(program_env, qtbot):
    w, _ = program_env(make_insights)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.preview_view.state is ViewState.NOT_CONNECTED)
    w.consent_box.setChecked(True)
    assert not w.analyze_button.isEnabled()
    w.analyze()
    assert w.results_view.state is ViewState.EMPTY


def test_not_enabled_before_preview_loads(program_env, qtbot):
    w, _ = program_env(make_insights, demo_provider_set())
    qtbot.addWidget(w)
    assert w.preview_view.state is ViewState.CONTENT            # the pre-load default is not "loaded"
    w.consent_box.setChecked(True)
    assert not w.analyze_button.isEnabled()


def test_preview_shows_aggregates_only(demo):
    w, _ = demo
    text = w.preview.toPlainText()
    assert '"analysis": "insights"' in text and '"accounts_in_scope"' in text and '"resource_counts"' in text
    assert "arn:" not in text and "111111111111" not in text


def test_consent_gates_analyze(demo, qtbot):
    w, spy = demo
    assert not w.analyze_button.isEnabled()
    w.consent_box.setChecked(True)
    assert w.analyze_button.isEnabled()
    w.analyze_button.click()
    qtbot.waitUntil(lambda: w.results_view.state is ViewState.CONTENT)
    text = w.results.toPlainText()
    assert text.startswith("Spend\n• ") and "• " in text
    assert w.model_label.text().endswith("demo (no model called)")
    assert not w._running and w.consent_box.isChecked() and w.analyze_button.isEnabled()   # same preview: may re-run
    assert spy.calls and spy.calls[0][1] == "insights"


def test_results_are_plain_text(demo):
    from PySide6.QtCore import Qt
    w, _ = demo
    assert w.results.isReadOnly() and w.model_label.textFormat() is Qt.TextFormat.PlainText


def test_changing_kind_resets(demo, qtbot):
    w, _ = demo
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: w.results_view.state is ViewState.CONTENT)
    w.kind_combo.setCurrentIndex(w.kind_combo.findData("advice"))
    assert not w.consent_box.isChecked() and w.results_view.state is ViewState.EMPTY
    assert w.results.toPlainText() == "" and w.model_label.text() == "" and not w.analyze_button.isEnabled()
    qtbot.waitUntil(lambda: '"analysis": "advice"' in w.preview.toPlainText())
    assert not w.consent_box.isChecked() and not w.analyze_button.isEnabled()


def test_preview_reload_resets_consent(demo, qtbot):
    w, _ = demo
    w.consent_box.setChecked(True)
    w.refresh()
    assert not w.consent_box.isChecked() and not w.analyze_button.isEnabled()
    qtbot.waitUntil(lambda: w.analyze_button.isEnabled() is False and w.consent_box.isEnabled())
    assert not w.consent_box.isChecked()


def test_inflight_analyze_cannot_land_under_new_kind(program_env, qtbot):
    spy = Spy(demo_provider_set().insights, hold=True)
    w, spy = _make(program_env, qtbot, spy)
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: bool(spy.calls))
    assert w.results_view.state is ViewState.LOADING and not w.analyze_button.isEnabled()
    w.kind_combo.setCurrentIndex(w.kind_combo.findData("advice"))
    spy.gate.set()
    qtbot.waitUntil(spy.finished.is_set)
    qtbot.wait(100)
    assert w.results_view.state is ViewState.EMPTY and w.results.toPlainText() == "" and w.model_label.text() == ""
    qtbot.waitUntil(lambda: '"analysis": "advice"' in w.preview.toPlainText())
    assert not w.analyze_button.isEnabled()          # the old completion must not re-arm or un-gate anything


def test_late_old_completion_does_not_rearm_new_run(program_env, qtbot):
    class TwoGate(Spy):
        def __init__(self, inner):
            super().__init__(inner, hold=True)
            self.gates, self.done = [threading.Event(), threading.Event()], [threading.Event(), threading.Event()]

        def analyze(self, scope, kind="insights", payload=None, *, cancel=None):
            i = len(self.calls)
            self.calls.append((scope, kind))
            self.gates[i].wait(5)
            try:
                return self.inner.analyze(scope, kind, payload, cancel=cancel)
            finally:
                self.done[i].set()

    spy = TwoGate(demo_provider_set().insights)
    w, spy = _make(program_env, qtbot, spy)
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: len(spy.calls) == 1)
    w.refresh()
    qtbot.waitUntil(lambda: w.consent_box.isEnabled())
    w.consent_box.setChecked(True)
    w.analyze_button.click()                         # a second run starts while the first is still held
    qtbot.waitUntil(lambda: len(spy.calls) == 2)
    spy.gates[0].set()                               # release the OLD call after the new run started
    qtbot.waitUntil(spy.done[0].is_set)
    qtbot.wait(100)
    assert w._running and w.results_view.state is ViewState.LOADING and not w.analyze_button.isEnabled()
    spy.gates[1].set()
    qtbot.waitUntil(lambda: w.results_view.state is ViewState.CONTENT)
    assert not w._running


def test_analyze_receives_exactly_the_displayed_payload(demo, qtbot):
    import json
    w, spy = demo
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: bool(spy.payloads))
    assert spy.payloads[0] == json.loads(w.preview.toPlainText()) and spy.payloads[0] is w._payload


def test_payload_cannot_change_without_consent_reset(demo, qtbot):
    w, spy = demo
    w.consent_box.setChecked(True)
    w.kind_combo.setCurrentIndex(w.kind_combo.findData("oddities"))      # a new preview is loading
    assert not w.consent_box.isChecked() and w._payload is None and w.preview.toPlainText() == ""
    w.analyze()
    assert spy.calls == []
    qtbot.waitUntil(lambda: '"analysis": "oddities"' in w.preview.toPlainText())
    assert not w.consent_box.isChecked() and not w.analyze_button.isEnabled()
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: bool(spy.payloads))
    assert spy.calls[0][1] == "oddities" and spy.payloads[0]["analysis"] == "oddities"


def test_analysis_start_clears_old_results(demo, qtbot):
    w, spy = demo
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: w.results_view.state is ViewState.CONTENT)
    spy.gate.clear()
    w.analyze_button.click()
    qtbot.waitUntil(lambda: len(spy.calls) == 2)
    assert w.results.toPlainText() == "" and w.model_label.text() == ""
    spy.gate.set()
    qtbot.waitUntil(lambda: w.results_view.state is ViewState.CONTENT)


def test_superseded_analysis_is_cancelled(program_env, qtbot):
    spy = Spy(demo_provider_set().insights, hold=True)
    w, spy = _make(program_env, qtbot, spy)
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: bool(spy.calls))
    handle = w._analysis
    w.refresh()
    assert handle.token.cancelled
    assert w._analysis is None
    spy.gate.set()


def test_failed_analyze_rearms_button(program_env, qtbot):
    spy = Spy(demo_provider_set().insights, fail=True)
    w, spy = _make(program_env, qtbot, spy)
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: w.results_view.state is ViewState.ERROR)
    assert w.analyze_button.isEnabled() and w.model_label.text() == ""


def test_error_result_clears_model_label(program_env, qtbot):
    class Err(Spy):
        def analyze(self, scope, kind="insights", payload=None, *, cancel=None):
            self.calls.append((scope, kind))
            return Result.error("model unavailable")

    w, _ = _make(program_env, qtbot, Err(demo_provider_set().insights))
    w.consent_box.setChecked(True)
    w.analyze_button.click()
    qtbot.waitUntil(lambda: w.results_view.state is ViewState.ERROR)
    assert w.model_label.text() == "" and w.analyze_button.isEnabled()


def test_scope_change_reloads_and_resets(demo, qtbot):
    w, _ = demo
    w.consent_box.setChecked(True)
    region = next(iter(w.scope_bar._region_actions))
    w.scope_bar.set_regions([region])
    assert w.scope.regions == (region,)
    assert not w.consent_box.isChecked() and w.results_view.state is ViewState.EMPTY


def test_menu_action_follows_gate(demo):
    w, _ = demo
    assert not w.analyze_action.isEnabled()
    w.consent_box.setChecked(True)
    assert w.analyze_action.isEnabled()


def test_no_duplicate_mnemonics(demo):
    w, _ = demo
    bar = [_letter(a.text()) for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar)), bar
    own = [_letter(a.text()) for a in w.program_menu.actions()]
    assert not (set(own) - {None}) & set(bar), own
    window = [_letter(w.analyze_button.text()), _letter(w.consent_box.text())]
    window += [_letter(lb.text()) for lb in w.findChildren(QLabel)]
    window += [_letter(a.text()) for a in w.program_menu.actions()]
    window = [x for x in window if x]
    assert len(window) == len(set(window)), window
    assert not set(window) & set(bar), window
