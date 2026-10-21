"""AI Insights: pick an analysis, review exactly what would be sent (aggregates only), then analyze (spec §7).
Live (ai-insights spec §7): a model per account, an estimate line, one answer per account, Export to Markdown."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import partial
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)
from shiboken6 import isValid

from aws95.contracts.common import Scope, Status
from aws95.contracts.insights import InsightReport, estimate_tokens
from aws95.core.sanitize import clean_model_text
from aws95.core.tasks import TaskHandle
from aws95.kit.widgets.jsonview import JsonView
from aws95.kit.widgets.stateview import StateView, ViewState
from aws95.programs.common import ProgramBase, write_text

KINDS = (("insights", "Insights"), ("oddities", "Oddities"), ("improvements", "Improvements"), ("advice", "Advice"))
_EMPTY_HINT = "Review the payload, tick the box, then press Analyze."
ESTIMATE = ("{n} calls · about {t} input tokens each · up to 1,500 output tokens each. "
            "Bedrock charges per token for the model you choose.")


class InsightsProgram(ProgramBase):
    TITLE = "AI Insights"
    PROGRAM_MENU = "&Insights"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._running = False
        self._preview_loaded = False            # set only when a preview result rendered (StateView starts CONTENT)
        self._previewed: tuple[Scope, str, Mapping[str, Any]] | None = None   # scope, kind and payload the user saw
        self._payload: Mapping[str, Any] | None = None     # the preview Result.data last rendered
        self._analysis: TaskHandle | None = None
        self._analysis_id = 0
        self.kind_combo = QComboBox()
        for key, title in KINDS:
            self.kind_combo.addItem(title, key)
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        kind_label = QLabel("Analysis &type:")
        kind_label.setBuddy(self.kind_combo)

        self.preview = JsonView()
        self.preview_view = StateView(self.preview)
        self.primary_view = self.preview_view
        preview_box = QGroupBox("What will be sent (aggregates only)")
        QVBoxLayout(preview_box).addWidget(self.preview_view)
        # Live: one model per account (remembered in Settings), and the cost line (ai-insights spec §7).
        self.model_pickers: dict[str, QComboBox] = {}
        self._labels: dict[str, str] = {}                    # "Account N" -> account id, local only
        self._last_report: InsightReport | None = None
        self.models_box = QGroupBox("Model per account")
        self.models_form = QFormLayout(self.models_box)
        self.refresh_models_button = QPushButton("Refresh &models")
        self.refresh_models_button.clicked.connect(self.refresh_models)
        self.estimate_label = QLabel("")
        self.estimate_label.setTextFormat(Qt.TextFormat.PlainText)
        self.estimate_label.setWordWrap(True)
        for w in (self.models_box, self.refresh_models_button, self.estimate_label):
            w.hide()                                     # shown only for a live per-account payload
        self.consent_box = QCheckBox("I have reviewed &what will be sent")
        self.consent_box.toggled.connect(self._sync_button)
        self.analyze_button = QPushButton("Anal&yze")
        self.analyze_button.clicked.connect(self.analyze)

        self.results = QPlainTextEdit()          # untrusted model text: plain text only, never rich
        self.results.setReadOnly(True)
        self.results_view = StateView(self.results)
        self.results_view.show_empty(_EMPTY_HINT)
        self.model_label = QLabel("")
        self.model_label.setTextFormat(Qt.TextFormat.PlainText)
        results_box = QGroupBox("Results")
        rl = QVBoxLayout(results_box)
        rl.addWidget(self.results_view)
        rl.addWidget(self.model_label)

        top = QHBoxLayout()
        top.addWidget(kind_label)
        top.addWidget(self.kind_combo)
        top.addStretch(1)
        top.addWidget(self.consent_box)
        top.addWidget(self.analyze_button)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(preview_box)
        split.addWidget(results_box)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        models_row = QHBoxLayout()
        models_row.addWidget(self.models_box, 1)
        models_row.addWidget(self.refresh_models_button, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(models_row)
        lay.addWidget(self.estimate_label)
        lay.addWidget(split, 1)
        self.set_content(body)
        self.analyze_action = self.program_menu.addAction("R&un analysis")
        self.analyze_action.triggered.connect(self.analyze)
        self._sync_button()

    def _kind(self) -> str:
        return self.kind_combo.currentData()

    def refresh(self) -> None:
        """Any reload (kind, scope, F5) voids the consent and any analysis in flight (M3-R4)."""
        self._analysis_id += 1
        self._cancel_analysis()
        self._running = False
        self._payload = None
        self.preview.clear()
        self._preview_loaded = False
        self._previewed = None
        self.invalidate(self.results_view)
        self.consent_box.setChecked(False)
        self.results.clear()
        self.results_view.show_empty(_EMPTY_HINT)
        self.model_label.setText("")
        self._last_report = None
        scope, kind = self.scope, self._kind()
        self.load(self.preview_view, self.ctx.providers.insights.preview_payload, scope, kind,
                  render=self._render_preview, on_result=partial(self._previewed_ok, scope, kind))
        self._sync_button()

    def _render_preview(self, payload: Mapping[str, Any]) -> None:
        self.preview.set_data(payload)
        self._payload = payload                         # the very object shown is the object analyzed
        self._build_pickers(payload)

    # ----- models and the cost line (ai-insights spec §5, §7) -----
    def _accounts(self) -> tuple[str, ...]:
        return tuple(self.scope.accounts) or tuple(e.account_id for e in self.ctx.settings.roster())

    def _nickname(self, account_id: str) -> str:
        named = self.ctx.settings.account_label(account_id)
        return named[0] if named else account_id

    def _build_pickers(self, payload: Mapping[str, Any]) -> None:
        entries = list(payload.get("accounts") or ())
        while self.models_form.rowCount():
            self.models_form.removeRow(0)
        self.model_pickers, self._labels = {}, {}
        if not entries:                                  # demo payloads have no per-account entries
            for w in (self.models_box, self.refresh_models_button, self.estimate_label):
                w.hide()
            self.estimate_label.setText("")
            return
        for w in (self.models_box, self.refresh_models_button, self.estimate_label):
            w.show()
        for entry, account in zip(entries, self._accounts()):
            self._labels[entry["label"]] = account
            combo = QComboBox()
            saved = self.ctx.settings.insights_model(account)
            if saved:
                combo.addItem(saved, saved)
            else:
                combo.addItem("Choose a model (Refresh models)", "")
            combo.currentIndexChanged.connect(partial(self._model_chosen, account))
            label = QLabel(f"{entry['label']} — {self._nickname(account)}")
            label.setTextFormat(Qt.TextFormat.PlainText)
            self.models_form.addRow(label, combo)
            self.model_pickers[account] = combo
        self.estimate_label.setText(ESTIMATE.format(n=len(entries),
                                                    t=f"{max(estimate_tokens(e) for e in entries):,}"))

    def refresh_models(self, *_args) -> None:
        for account in list(self.model_pickers):
            handle = self.ctx.runner.submit(self.ctx.providers.insights.models, self.scope, account)
            self._tasks.track(handle)
            handle.finished.connect(partial(self._models_loaded, account))

    def _models_loaded(self, account: str, result) -> None:
        combo = self.model_pickers.get(account)
        if combo is None or not isValid(combo):
            return
        if result.status is not Status.OK or result.data is None:
            self.estimate_label.setText(f"{self._nickname(account)}: {result.message or 'models could not be listed.'}")
            return
        saved = self.ctx.settings.insights_model(account)
        combo.blockSignals(True)
        try:
            combo.clear()
            for choice in result.data:
                combo.addItem(choice.name, choice.model_id)
            index = combo.findData(saved) if saved else -1
            combo.setCurrentIndex(index if index >= 0 else -1)
        finally:
            combo.blockSignals(False)
        self._sync_button()

    def _model_chosen(self, account: str, index: int) -> None:
        combo = self.model_pickers.get(account)
        if combo is not None and index >= 0 and combo.itemData(index):
            self.ctx.settings.set_insights_model(account, combo.itemData(index))
        self._sync_button()

    def _cancel_analysis(self) -> None:
        handle, self._analysis = self._analysis, None
        if handle is not None and isValid(handle):
            handle.cancel()

    def _previewed_ok(self, scope: Scope, kind: str, result) -> None:
        if result.data is not None and self._payload is not None and self.preview_view.state is ViewState.CONTENT:
            self.consent_box.setChecked(False)          # consent is bound to this exact preview
            self._preview_loaded = True
            self._previewed = (scope, kind, self._payload)
        self._sync_button()

    def _on_kind(self, _index: int) -> None:
        self.refresh()

    def _sync_button(self, *_args) -> None:
        self.consent_box.setEnabled(self._preview_loaded)
        has_model = not self.model_pickers or any(self.ctx.settings.insights_model(a) for a in self.model_pickers)
        ready = (self._preview_loaded and self.preview_view.state is ViewState.CONTENT
                 and self.consent_box.isChecked() and not self._running and has_model)
        self.analyze_button.setEnabled(ready)
        self.analyze_action.setEnabled(ready)

    def analyze(self) -> None:
        if not self.analyze_button.isEnabled() or self._previewed is None:
            return
        scope, kind, payload = self._previewed         # analyze exactly what was previewed
        self._running = True
        self.results.clear()
        self.model_label.setText("")
        self._sync_button()
        aid = self._analysis_id
        handle = self.load(self.results_view, self.ctx.providers.insights.analyze, scope, kind, payload,
                           render=self._render, on_result=partial(self._done, aid),
                           loading_text="Analyzing…", status=False)
        self._analysis = handle
        handle.failed.connect(partial(self._failed, aid))   # an exception skips on_result

    def _render(self, report: InsightReport) -> None:
        self._last_report = report
        if report.accounts:
            self._render_accounts(report)
            return
        blocks = [clean_model_text(section.title) + "\n" + "\n".join(f"• {clean_model_text(b)}" for b in section.bullets)
                  for section in report.sections]
        self.results.setPlainText("\n\n".join(blocks))
        self.model_label.setText(f"Model: {clean_model_text(report.model)}")

    def _render_accounts(self, report: InsightReport) -> None:
        blocks = []
        for acct in report.accounts:
            account = self._labels.get(acct.label)
            head = acct.label + (f" — {self._nickname(account)}" if account else "") + \
                (f" — {clean_model_text(acct.model)}" if acct.model else "")
            if acct.error:
                blocks.append(f"{head}\n{clean_model_text(acct.error)}")
                continue
            body = "\n\n".join(clean_model_text(sec.title) + "\n" +
                                "\n".join(f"• {clean_model_text(b)}" for b in sec.bullets) for sec in acct.sections)
            usage = f"{acct.input_tokens:,} input · {acct.output_tokens:,} output tokens, as reported by Bedrock"
            blocks.append(f"{head}\n{body}\n{usage}")
        self.results.setPlainText("\n\n".join(blocks))
        self.model_label.setText(f"Models: {clean_model_text(report.model)}")

    def export(self) -> bool:
        """Markdown with provenance and the exact payload sent; account labels only, never nicknames or ids."""
        if self._last_report is None or self._previewed is None:
            self.status.set_counts("Nothing to export")
            return False
        _scope, kind, payload = self._previewed
        lines = ["# AI Insights", "", f"> Analysis: {kind}", ">",
                 f"> Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC", ">",
                 f"> Models: {clean_model_text(self._last_report.model)}", "", "## Payload sent", "", "```json",
                 json.dumps(payload, indent=2, sort_keys=True), "```", ""]
        for acct in self._last_report.accounts:
            lines += [f"## {acct.label}", ""]
            if acct.error:
                lines += [f"Error: {clean_model_text(acct.error)}", ""]
            for sec in acct.sections:
                lines += [f"### {clean_model_text(sec.title)}", *[f"- {clean_model_text(b)}" for b in sec.bullets], ""]
        path = self.ask_save_path(self, "Export AI Insights", "ai-insights.md", "Markdown (*.md)")
        if not path:
            return False
        try:
            write_text(path, "\n".join(lines))
        except (OSError, ValueError) as exc:
            self.status.set_counts(f"Export failed: {exc}")
            return False
        self.status.set_counts(f"Saved {path}")
        return True

    def _done(self, analysis_id: int, result) -> None:
        if analysis_id != self._analysis_id:
            return
        self._running = False
        self._sync_button()
        if result.status is Status.ERROR or result.data is None:
            self.model_label.setText("")

    def _failed(self, analysis_id: int, *_args) -> None:
        if analysis_id != self._analysis_id:
            return
        self._running = False
        self._sync_button()


def make_insights(ctx) -> InsightsProgram:
    return InsightsProgram(ctx)
