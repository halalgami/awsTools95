"""My Accounts: profiles, nickname/colour, default regions, verify, collector health (spec §7)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea, QSplitter, QVBoxLayout,
                               QWidget)
from shiboken6 import isValid

from aws95.contracts.accounts import NOT_IN_CONFIG, Profile, RosterEntry
from aws95.contracts.billing import BILLING_COST_NOTE
from aws95.contracts.common import Result, Scope, Status
from aws95.core.sanitize import clean_model_text
from aws95.core.settings import MAX_NICKNAME
from aws95.kit.icons import icon
from aws95.kit.widgets.dialogs import Icon, MessageBox95
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView
from aws95.programs.accounts.rescan import DEMO_TIP, NewProfilesDialog, VerifyQueue
from aws95.programs.common import REGIONS, ProgramBase

PROFILE_COLUMNS = [Column("use", "Use", "text", -1), Column("name", "Profile", "text", -1),
                   Column("kind", "Kind", "text", -1), Column("account_id", "Account", "text", -1),
                   Column("nickname", "Nickname", "text", -1), Column("region", "Region", "text", -1),
                   Column("status", "Status")]
HEALTH_COLUMNS = [Column("collector", "Collector"), Column("status", "Status", "status"), Column("error", "Error"),
                  Column("duration_ms", "ms", "number"), Column("regions", "Regions covered"),
                  Column("denied", "Denied", "number")]


def _short(path: str) -> str:
    """The home directory shown as ~ (spec §6.3)."""
    home = os.path.expanduser("~")
    if home not in ("", "~", "/") and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def _plain(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class AccountsProgram(ProgramBase):
    TITLE = "My Accounts"
    PROGRAM_MENU = "&Accounts"
    EXPORT_NAME = "accounts.csv"
    # Qt's own dialog, parented to the window: the native macOS panel drops this window behind the main one.
    ask_color = staticmethod(lambda parent, initial: QColorDialog.getColor(
        initial, parent.window(), "Account colour", QColorDialog.ColorDialogOption.DontUseNativeDialog))
    clock = staticmethod(lambda: datetime.now(timezone.utc))

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent, scope_bar=False)
        self._color = "#c0c0c0"
        self._reselect: str | None = None
        self._profiles: tuple[Profile, ...] = ()
        self._checks: dict[str, Result] = {}        # this session's verify results, by profile name
        self._rescan_gen = 0
        self._pending_profiles = None
        self._queue: VerifyQueue | None = None
        self.new_profiles_dialog: NewProfilesDialog | None = None
        self._quiet = False                          # a status-only row refresh: selection signals are ignored
        self._shown_key: tuple | None = None
        self._note = ""
        self._counts: tuple[int, int] = (0, 0)
        self._looking_in = ""
        self._demo = bool(ctx.providers.is_demo)

        self.header_label = _plain("Looking in: …", self)
        self.header_label.setContentsMargins(4, 2, 4, 2)
        self.header_label.setWordWrap(True)          # a long config path wraps; it never widens the window
        self._header_view = StateView(QLabel(), self)    # keys the config_sources load; never shown
        self._header_view.hide()
        self.profiles = DataGrid(PROFILE_COLUMNS, placeholder="Filter…  e.g. kind:profile")
        self.profiles.set_column_visible("region", False)       # room for Status; View > columns shows it again
        self.profiles_view = StateView(self.profiles)
        self.primary_view = self.profiles_view
        self.profiles.current_row_changed.connect(self._on_profile_row)
        self.profiles.model.filter_applied.connect(self._sync_counts)

        self.detail = QWidget()
        d = QVBoxLayout(self.detail)
        self.title_label = _plain("Select a profile", self.detail)

        label_box = QGroupBox("&Display", self.detail)
        form = QFormLayout(label_box)
        self.nickname_edit = QLineEdit(label_box)
        self.nickname_edit.setMaxLength(MAX_NICKNAME)
        self.color_button = QPushButton("Colour…", label_box)
        self.color_button.clicked.connect(self._pick_color)
        self.apply_label_button = QPushButton("Appl&y", label_box)
        self.apply_label_button.clicked.connect(self._apply_label)
        self.reset_label_button = QPushButton("Re&set", label_box)
        self.reset_label_button.clicked.connect(self._reset_label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_label_button)
        buttons.addWidget(self.reset_label_button)
        form.addRow("&Nickname:", self.nickname_edit)
        colour_label = QLabel("&Colour:", label_box)
        colour_label.setBuddy(self.color_button)
        form.addRow(colour_label, self.color_button)
        form.addRow(buttons)

        region_box = QGroupBox("Default reg&ions", self.detail)
        rl = QVBoxLayout(region_box)
        self.region_list = QListWidget(region_box)
        for code in REGIONS:
            item = QListWidgetItem(code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.region_list.addItem(item)
        self.apply_regions_button = QPushButton("Apply re&gions", region_box)
        self.apply_regions_button.clicked.connect(self._apply_regions)
        rl.addWidget(self.region_list)
        rl.addWidget(self.apply_regions_button)
        billing_box = QGroupBox("Billing", self.detail)
        bl = QVBoxLayout(billing_box)
        self.billing_check = QCheckBox("Include &Billing in snapshots", billing_box)
        self.billing_check.toggled.connect(self._billing_toggled)
        billing_note = _plain(BILLING_COST_NOTE, billing_box)
        billing_note.setWordWrap(True)
        bl.addWidget(self.billing_check)
        bl.addWidget(billing_note)

        self.active_button = QPushButton("Set as ac&tive account", self.detail)
        self.active_button.clicked.connect(self._set_active)
        self.verify_button = QPushButton("Ve&rify", self.detail)
        self.verify_button.clicked.connect(self.verify)
        self.verify_label = _plain("", self.detail)
        self.verify_label.setWordWrap(True)
        self.verify_view = StateView(self.verify_label)
        self.verify_view.show_empty("Press Verify to check this profile's identity.")

        health_box = QGroupBox("C&ollector health", self.detail)
        hl = QVBoxLayout(health_box)
        self.health = DataGrid(HEALTH_COLUMNS, placeholder="Filter…  e.g. status:denied")
        self.health_view = StateView(self.health)
        self.health_view.show_empty("Select a verified profile to see collector health.")
        hl.addWidget(self.health_view)

        row = QHBoxLayout()
        row.addWidget(self.active_button)
        row.addWidget(self.verify_button)
        d.addWidget(self.title_label)
        d.addWidget(label_box)
        d.addWidget(region_box)
        d.addWidget(billing_box)
        d.addLayout(row)
        d.addWidget(self.verify_view)
        d.addWidget(health_box, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.detail)
        # The detail pane must never clip sideways at large font scales: reserve its own minimum width (the grid
        # on the left scrolls instead).
        scroll.setMinimumWidth(self.detail.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width()
                               + 2 * scroll.frameWidth())

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(2)
        ll.addWidget(self.header_label)
        ll.addWidget(self.profiles_view, 1)
        split.addWidget(left)
        split.addWidget(scroll)
        split.setStretchFactor(0, 5)        # the profiles grid carries seven columns, Status last
        split.setStretchFactor(1, 2)
        split.setSizes([590, 310])
        self.set_content(split)

        self.verify_action = self.program_menu.addAction("&Verify")
        self.verify_action.triggered.connect(self.verify)
        self.active_action = self.program_menu.addAction("Set as Active &Account")
        self.active_action.triggered.connect(self._set_active)
        self.program_menu.addSeparator()
        self.rescan_action = self._action("&Rescan Config\tF5", self.rescan, tip="Re-read the AWS config and check profiles")
        self.use_action = self._action("&Use This Profile", self.toggle_use, tip="Add to or remove from your accounts")
        self.permissions_action = self._action("Check &Permissions…", self.check_permissions, "shield")
        self.sign_in_action = self._action("S&ign In…", self.sign_in, "run")
        self.setup_action = self._action("Run &Setup…", self.run_setup, "gear")
        self.remove_action = self._action("Re&move from List", self.remove_missing,
                                          tip="Remove a profile that is no longer in the AWS config")
        for act, text in ((self.permissions_action, "Permissions"), (self.sign_in_action, "Sign In"),
                          (self.setup_action, "Setup"), (self.remove_action, "Remove")):
            act.setIconText(text)
            self.toolbar.addAction(act)
        self.use_action.setShortcut(Qt.Key.Key_Space)       # only while the profiles table has focus
        self.use_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.profiles.view.addAction(self.use_action)
        self.profiles.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.profiles.view.customContextMenuRequested.connect(self._row_menu)
        if self._demo:
            for act in (self.use_action, self.remove_action, self.active_action):
                act.setToolTip(DEMO_TIP)
                act.setStatusTip(DEMO_TIP)
            for w in (self.nickname_edit, self.color_button, self.apply_label_button, self.reset_label_button,
                      self.region_list, self.apply_regions_button, self.active_button, self.billing_check):
                w.setToolTip(DEMO_TIP)             # demo never writes settings (spec §2)
        self.ctx.settings.changed.connect(self._on_setting)
        self.ctx.shell.sign_in_succeeded.connect(self._on_sign_in_succeeded)
        self._confirm: MessageBox95 | None = None
        self._set_detail_enabled(False)
        self._update_actions(None)

    # ----- loading -----
    def _action(self, text: str, slot, icon_name: str | None = None, *, tip: str = "") -> QAction:
        act = self.program_menu.addAction(icon(icon_name), text) if icon_name else self.program_menu.addAction(text)
        act.triggered.connect(slot)
        if tip:
            act.setToolTip(tip)
        return act

    def refresh(self) -> None:
        """F5 and the toolbar Refresh: re-read the config and check profiles (the only network path)."""
        self.rescan()

    def _first_load(self) -> None:
        if isValid(self):
            self._reload()              # opening the window makes no network calls

    def _on_setting(self, key: str) -> None:
        if key == "accounts/roster" and isValid(self):
            self._reload()

    def _reload(self, on_profiles=None) -> None:
        """Re-read the config sources and the profile list. Makes no network calls."""
        if on_profiles is not None:         # survives a superseding reload (first load, roster change)
            self._pending_profiles = on_profiles
        current = self.selected_profile()
        self._reselect = current.name if current is not None else None
        self._profiles = ()
        self.profiles.set_rows([])          # the old profiles must not sit under a new load (M3-R5)
        self.load(self._header_view, self.ctx.providers.accounts.config_sources, render=lambda _data: None,
                  on_result=self._render_header, status=False)
        self.load(self.profiles_view, self.ctx.providers.accounts.list_profiles, Scope(), render=self._render_profiles,
                  on_result=self._profiles_loaded)

    def _profiles_loaded(self, result: Result) -> None:
        callback, self._pending_profiles = self._pending_profiles, None
        if callback is not None:
            callback(result)

    def _render_header(self, result: Result) -> None:
        if result.data is None or result.status in (Status.ERROR, Status.UNREACHABLE, Status.NOT_CONNECTED):
            self.header_label.setText("Looking in: ✖ " + (result.message or "config unavailable"))
            self._looking_in = ""
        else:
            sources = result.data
            self.header_label.setText("Looking in: " + " · ".join(
                f"{_short(src.path)} {'✔' if src.exists else '✖'}" for src in sources))
            self._looking_in = _short(os.path.dirname(sources[0].path)) if sources else ""
        self._sync_counts(*self._counts)

    # ----- status of a row -----
    def _in_use(self, p: Profile) -> bool:
        if self._demo:
            return p.selected
        return any(e.profile == p.name for e in self.ctx.settings.roster())

    def _known_account(self, p: Profile | None) -> str | None:
        """The account behind a profile: this session's check, else the roster's stored one."""
        if p is None:
            return None
        check = self._checks.get(p.name)
        if check is not None and check.status is Status.OK and check.data is not None:
            return check.data.account_id
        return p.verified.account_id if p.verified else None

    def status_of(self, profile: Profile | str) -> str:
        p = profile if isinstance(profile, Profile) else next((x for x in self._profiles if x.name == profile), None)
        if p is None:
            return ""
        if p.issue == NOT_IN_CONFIG:
            return "⊘ Not in config"
        if p.verify == "never":
            return f"— {p.issue or 'Cannot be checked'}"
        check = self._checks.get(p.name)
        if check is not None:
            if check.status is Status.OK:
                return "✔ Verified"
            if check.status is Status.NOT_CONNECTED and p.sign_in_profile:
                return "⚠ Sign-in needed"
            return f"✖ {check.message or 'Check failed'}"
        remembered = self._remembered(p)
        if remembered is not None:
            return f"✔ Verified {remembered}"
        return "● In use (not checked yet)" if self._in_use(p) else "○ Not selected"

    def _remembered(self, p: Profile) -> str | None:
        """The relative age of the last stored verification, when it still matches the roster's account."""
        if self._demo or p.issue:
            return None
        stored = self.ctx.settings.last_verified(p.name)
        if stored is None:
            return None
        account, at = stored
        entry = next((e for e in self.ctx.settings.roster() if e.profile == p.name), None)
        if entry is None:
            return None
        if entry.account_id != account:
            self.ctx.settings.forget_verified(p.name)       # the profile now points at another account
            return None
        seconds = int((self.clock() - at).total_seconds())
        if seconds < -300:                                  # stored in the future: the clock or the store is wrong
            self.ctx.settings.forget_verified(p.name)
            return None
        seconds = max(0, seconds)
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60} min ago"
        if seconds < 86400:
            return f"{seconds // 3600} h ago"
        days = seconds // 86400
        return "1 day ago" if days == 1 else f"{days} days ago"

    def _rows(self) -> list[dict]:
        rows = []
        for p in self._profiles:
            acct = self._known_account(p)
            label = self.ctx.settings.account_label(acct) if acct else None
            ref = p.verified
            nick = (label or ((ref.nickname, ref.color) if ref else ("", "")))[0] if acct else ""
            rows.append({"use": "☑" if self._in_use(p) else "☐", "name": p.name, "kind": p.kind,
                         "region": p.region or "", "account_id": acct or "", "nickname": nick,
                         "status": self.status_of(p), "profile": p})
        return rows

    def _render_profiles(self, profiles: tuple[Profile, ...]) -> None:
        self._profiles = tuple(profiles)
        self.profiles.set_rows(self._rows())
        if self._reselect:                 # keep the user's selection across a reload
            self.select_profile(self._reselect)
            self._reselect = None

    def _refresh_rows(self) -> None:
        """Re-render statuses after a check, keeping the selection and the detail panel as they are."""
        current = self.selected_profile()
        name = current.name if current is not None else None
        self._quiet = True
        try:
            self.profiles.set_rows(self._rows())
            if name:
                self.select_profile(name)
        finally:
            self._quiet = False
        p = self.selected_profile()
        key = (p.name, self._known_account(p)) if p is not None else None
        if key != self._shown_key:
            self._on_profile_row(self.profiles.current_row(), keep_verify=True)
        else:
            self._update_actions(p)

    def _sync_counts(self, shown: int, total: int) -> None:
        """Counts describe the visible rows (M3-R8)."""
        self._counts = (shown, total)
        parts = [f"{shown} of {total} profiles"] if total else []
        if self._note:
            parts.append(self._note)
        if self._looking_in:
            parts.append(f"Looking in {self._looking_in}")
        self.status.set_counts(" · ".join(parts))

    def _say(self, text: str) -> None:
        self._note = text
        self._sync_counts(*self._counts)

    def export_grid(self) -> DataGrid:
        return self.profiles

    # ----- selection -----
    def selected_profile(self) -> Profile | None:
        row = self.profiles.current_row()
        return row["profile"] if row else None

    def select_profile(self, name: str) -> bool:
        for i, row in enumerate(self.profiles.model.view_rows()):
            if row["name"] == name:
                while self.profiles.model.canFetchMore() and i >= self.profiles.model.rowCount():
                    self.profiles.model.fetchMore()
                self.profiles.view.selectRow(i)
                return True
        return False

    def _set_detail_enabled(self, on: bool) -> None:
        on = on and not self._demo
        for w in (self.nickname_edit, self.color_button, self.apply_label_button, self.reset_label_button,
                  self.region_list, self.apply_regions_button, self.active_button, self.billing_check):
            w.setEnabled(on)
        self.active_action.setEnabled(on)

    def _on_profile_row(self, row, *, keep_verify: bool = False) -> None:
        if self._quiet:
            return
        profile: Profile | None = row["profile"] if row else None
        self._shown_key = (profile.name, self._known_account(profile)) if profile is not None else None
        if not keep_verify:         # a status-only refresh must not blank a verify result that just arrived
            self.verify_view.show_empty("Press Verify to check this profile's identity.")
            self.invalidate(self.verify_view)   # a slower verify/health load must not land under this profile
        self.invalidate(self.health_view)
        self._update_actions(profile)
        acct = self._known_account(profile)
        ref = profile.verified if profile else None
        self._set_detail_enabled(acct is not None)
        if profile is None:
            self.title_label.setText("Select a profile")
            self.health_view.show_empty("Select a verified profile to see collector health.")
            return
        self.title_label.setText(f"Profile {profile.name}")
        if acct is None:
            self.nickname_edit.setText("")
            self.billing_check.blockSignals(True)
            self.billing_check.setChecked(False)
            self.billing_check.blockSignals(False)
            self.health_view.show_empty("Verify this profile to see collector health.")
            return
        if ref is None or ref.account_id != acct:         # checked this session: no stored nickname or colour yet
            ref = None
        nick, color = self.ctx.settings.account_label(acct) or ((ref.nickname, ref.color) if ref else ("", "#c0c0c0"))
        self.nickname_edit.setText(nick)
        self._show_color(color)
        chosen = set(self.ctx.settings.account_regions(acct))
        for i in range(self.region_list.count()):
            item = self.region_list.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.text() in chosen else Qt.CheckState.Unchecked)
        self.billing_check.blockSignals(True)
        self.billing_check.setChecked(self.ctx.settings.account_billing(acct))
        self.billing_check.blockSignals(False)
        self.load(self.health_view, self.ctx.providers.accounts.collector_health, Scope(accounts=(acct,)),
                  render=self._render_health, status=False)     # secondary: the profiles list owns the status bar

    def _render_health(self, rows) -> None:
        self.health.set_rows([{"collector": h.collector, "status": h.status.value, "error": h.error or "",
                               "duration_ms": h.duration_ms,
                               "regions": f"{len(h.coverage.regions_covered)}/{len(h.coverage.regions_requested)}",
                               "denied": h.coverage.denials}
                              for h in rows])

    # ----- actions -----
    def _billing_toggled(self, on: bool) -> None:
        acct = self._account_id()
        if acct is None or self._demo:
            return
        self.ctx.settings.set_account_billing(acct, on)
        self.status.set_counts("Billing will be included in this account's snapshots." if on
                               else "Billing is off for this account's snapshots.")

    def _account_id(self) -> str | None:
        return self._known_account(self.selected_profile())

    def _show_color(self, color: str) -> None:
        parsed = QColor(color)
        color = parsed.name() if parsed.isValid() else "#c0c0c0"     # provider text is never trusted
        self._color = color
        self.color_button.setProperty("color", color)
        self.color_button.setText(f"Colour {color}")
        swatch = QPixmap(16, 16)
        swatch.fill(Qt.GlobalColor.black)
        inner = QPixmap(14, 14)
        inner.fill(QColor(color))
        p = QPainter(swatch)
        p.drawPixmap(1, 1, inner)
        p.end()
        self.color_button.setIcon(QIcon(swatch))

    def _pick_color(self, *_args) -> None:
        chosen = self.ask_color(self, QColor(self._color))
        top = self.window()
        if isValid(top):
            top.raise_()
            top.activateWindow()
        if chosen.isValid():
            self._show_color(chosen.name())

    def _apply_label(self, *_args) -> None:
        acct = self._account_id()
        if acct is None or self._demo:
            return
        try:
            self.ctx.settings.set_account_label(acct, self.nickname_edit.text(), self._color)
        except ValueError as exc:
            self.status.set_counts(f"Not saved: {exc}")
            return
        self.status.set_counts("Nickname and colour saved")
        self._reload()          # labels are local: no network checks

    def _reset_label(self, *_args) -> None:
        acct = self._account_id()
        if acct is not None and not self._demo:
            self.ctx.settings.clear_account_label(acct)
            self._reload()

    def _apply_regions(self, *_args) -> None:
        acct = self._account_id()
        if acct is None or self._demo:
            return
        regions = [self.region_list.item(i).text() for i in range(self.region_list.count())
                   if self.region_list.item(i).checkState() == Qt.CheckState.Checked]
        self.ctx.settings.set_account_regions(acct, regions)
        self.status.set_counts("Default regions saved")

    def _set_active(self, *_args) -> None:
        acct = self._account_id()
        if acct is not None and not self._demo:
            self.ctx.settings.active_account = acct
            self.status.set_counts("Active account set")

    def verify(self, *_args) -> None:
        p = self.selected_profile()
        if p is not None and p.verify != "never":        # MFA profiles are never verified (the actions are disabled too)
            self.load(self.verify_view, self.ctx.providers.accounts.verify_profile, Scope(), p.name,
                      render=self._render_verify, loading_text="Verifying…", status=False,
                      on_result=partial(self._record_check, p.name))

    def _record_check(self, name: str, result: Result) -> None:
        if isValid(self):
            self._checks[name] = result
            self._remember(name, result)
            self._refresh_rows()

    def _remember(self, name: str, result: Result) -> None:
        """Persist a passed check (the window then opens already verified), drop the entry of a failed one."""
        if self._demo:
            return
        if result.status is Status.OK and result.data is not None:
            try:
                self.ctx.settings.record_verified(name, result.data.account_id, self.clock())
            except ValueError:
                pass                    # a name or account that can't be stored is simply not remembered
        else:
            self.ctx.settings.forget_verified(name)

    def _render_verify(self, ref) -> None:
        self.verify_label.setText(f"Verified: account {ref.account_id} ({ref.nickname}) via profile {ref.profile}")

    # ----- roster actions -----
    def _update_actions(self, p: Profile | None) -> None:
        status = self.status_of(p) if p is not None else ""
        usable = p is not None and p.verify != "never"      # MFA profiles are never verified or probed (spec §4.1)
        self.verify_button.setEnabled(usable)
        self.verify_action.setEnabled(usable)
        self.use_action.setEnabled(p is not None and not self._demo)
        self.permissions_action.setEnabled(usable and self._known_account(p) is not None)
        self.sign_in_action.setEnabled(status.startswith("⚠"))
        self.remove_action.setEnabled(status.startswith("⊘") and not self._demo)

    def _row_menu(self, pos) -> None:
        if self.selected_profile() is None:
            return
        menu = QMenu(self)
        for act in (self.use_action, self.permissions_action, self.sign_in_action, self.remove_action):
            menu.addAction(act)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(self.profiles.view.viewport().mapToGlobal(pos))

    def _write_roster(self, entries: list[RosterEntry]) -> bool:
        try:
            self.ctx.settings.set_roster(entries)
        except ValueError as exc:
            self._say(f"Not saved: {exc}")
            return False
        return True

    def toggle_use(self, *_args) -> None:
        p = self.selected_profile()
        if p is None or self._demo:
            return
        self._say("")
        roster = list(self.ctx.settings.roster())
        if any(e.profile == p.name for e in roster):
            self._write_roster([e for e in roster if e.profile != p.name])
            return
        acct = self._known_account(p)
        if acct is None:
            self._say(clean_model_text(p.issue or "This profile can't be used yet.") if p.verify == "never"
                      else "Verify this profile first.")
            return
        replaced = [e.profile for e in roster if e.account_id == acct]
        if replaced:
            self._ask_replace(p.name, acct, replaced[0])      # spec §6.3: confirm before replacing another profile
            return
        self._write_roster(roster + [RosterEntry(p.name, acct)])

    def _ask_replace(self, name: str, acct: str, old: str) -> None:
        if self._confirm is not None and isValid(self._confirm):
            self._confirm.close()
        box = MessageBox95(self, "Replace profile?", f"{old} already provides this account. Use {name} instead? "
                           "Each account is used once.", Icon.QUESTION, ("Yes", "No"), default=1)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._confirm = box
        self.mark_dialog(box)
        box.accepted.connect(partial(self._replace_answered, box, name, acct, old))
        box.show()
        box.raise_()
        box.activateWindow()

    def _replace_answered(self, box: MessageBox95, name: str, acct: str, old: str) -> None:
        if box.choice != 0 or not isValid(self):
            return
        roster = [e for e in self.ctx.settings.roster() if e.account_id != acct]
        if self._write_roster(roster + [RosterEntry(name, acct)]):
            self._say(f"{name} replaces {old}: one profile per account.")

    def remove_missing(self, *_args) -> None:
        p = self.selected_profile()
        if p is None or self._demo or p.issue != NOT_IN_CONFIG:
            return
        self._say("")
        self.ctx.settings.forget_verified(p.name)
        self._write_roster([e for e in self.ctx.settings.roster() if e.profile != p.name])

    def check_permissions(self, *_args) -> None:
        p = self.selected_profile()
        acct = self._known_account(p)
        if p is not None and acct is not None and p.verify != "never":
            self.ctx.shell.check_permissions(p.name, acct)

    def run_setup(self, *_args) -> None:
        self.ctx.shell.open_setup_wizard()

    # ----- sign in -----
    def sign_in(self, *_args) -> None:
        p = self.selected_profile()
        if p is None or not self.status_of(p).startswith("⚠"):
            return
        self._say(f"Signing in to {p.name}…")
        self.ctx.shell.sign_in(p.sign_in_profile or p.name)       # the shell's dialog shows the URL/code and Cancel

    def _on_sign_in_succeeded(self, cli_profile: str) -> None:
        if not isValid(self):
            return
        names = [p.name for p in self._profiles if (p.name == cli_profile or p.sign_in_profile == cli_profile)
                 and self.status_of(p).startswith("⚠")]
        if names:
            self._say(f"Signed in to {cli_profile}. Checking…")
        for name in names:
            self._verify_one(name)

    def _verify_one(self, name: str) -> None:
        handle = self.ctx.runner.submit(self.ctx.providers.accounts.verify_profile, Scope(), name)
        self._tasks.track(handle)
        handle.finished.connect(partial(self._record_check_noted, name))
        handle.failed.connect(partial(self._verify_failed, name))

    def _record_check_noted(self, name: str, result: Result) -> None:
        if isValid(self):
            self._say("")
            self._record_check(name, result)

    def _verify_failed(self, name: str, message: str, _details: str) -> None:
        self._record_check_noted(name, Result.error(f"Unexpected error: {message.split(':')[0]}"))

    # ----- rescan -----
    def rescan(self, *_args) -> None:
        """Re-read the config, verify roster and new profiles (at most 4 at a time), then offer what changed."""
        self._rescan_gen += 1
        if self._queue is not None:
            self._queue.cancel()
            self._queue = None
        self._note = ""
        self._reload(on_profiles=partial(self._rescan_profiles, self._rescan_gen))

    def _rescan_profiles(self, gen: int, result: Result) -> None:
        if gen != self._rescan_gen or not isValid(self) or result.data is None:
            return
        profiles = tuple(result.data)
        roster = {e.profile for e in self.ctx.settings.roster()}
        known = set(self.ctx.settings.known_profiles())
        names = [p.name for p in profiles if p.verify == "auto" and (p.name in roster or p.name not in known)]
        if names:
            self._say(f"Checking {len(names)} profile{'s' if len(names) != 1 else ''}…")
        self._queue = VerifyQueue(self, self.ctx.runner, self._tasks, self.ctx.providers.accounts.verify_profile,
                                  names, self._rescan_result, partial(self._rescan_done, gen, profiles, frozenset(names)))
        self._queue.start()

    def _rescan_result(self, name: str, result: Result) -> None:
        self._checks[name] = result
        self._remember(name, result)

    def _rescan_done(self, gen: int, profiles: tuple[Profile, ...], checked: frozenset[str]) -> None:
        if gen != self._rescan_gen or not isValid(self):
            return
        self._queue = None
        self._note = ""
        self._refresh_rows()
        if not self._demo:
            self.ctx.settings.set_known_profiles([p.name for p in profiles])
        roster = self.ctx.settings.roster()
        held = {e.account_id for e in roster}
        stored = {e.profile: e.account_id for e in roster}
        new: list[RosterEntry] = []
        changed: list[tuple[str, str, str]] = []
        for p in profiles:
            if p.name not in checked:       # only what THIS rescan verified: a cancelled offer is not repeated
                continue
            check = self._checks.get(p.name)
            if check is None or check.status is not Status.OK or check.data is None:
                continue
            acct = check.data.account_id
            if p.name in stored:
                if stored[p.name] != acct:
                    changed.append((p.name, acct, stored[p.name]))
            elif acct not in held and all(e.account_id != acct for e in new):
                new.append(RosterEntry(p.name, acct))
        if new or changed:
            self._open_new_profiles(new, changed)

    def _open_new_profiles(self, new: list[RosterEntry], changed: list[tuple[str, str, str]]) -> None:
        if self.new_profiles_dialog is not None and isValid(self.new_profiles_dialog):
            self.new_profiles_dialog.close()
        dlg = NewProfilesDialog(self, new, changed)
        self.new_profiles_dialog = dlg
        self.mark_dialog(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._rescan_gen += 1
        if self._queue is not None:
            self._queue.cancel()
            self._queue = None
        super().closeEvent(event)


def make_accounts(ctx) -> AccountsProgram:
    return AccountsProgram(ctx)
