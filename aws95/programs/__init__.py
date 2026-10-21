"""Every program's manifest. The shell builds Start menu, desktop icons, palette and shortcuts from this list."""
from __future__ import annotations

from aws95.shell.registry import ProgramManifest

DEFAULT_MANIFESTS: tuple[ProgramManifest, ...] = (
    ProgramManifest("accounts", "My Accounts", "computer", "programs", True, None, "singleton",
                    "aws95.programs.accounts:make_accounts", ("profiles", "sso", "account"), 10),
    ProgramManifest("resources", "Resources", "server", "programs", True, "Ctrl+Shift+R", "multi",
                    "aws95.programs.resources:make_resources", ("ec2", "s3", "lambda", "rds", "inventory"), 20),
    ProgramManifest("billing", "Billing", "money", "programs", True, "Ctrl+Shift+B", "singleton",
                    "aws95.programs.billing:make_billing", ("cost", "spend", "forecast"), 30),
    ProgramManifest("findings", "Findings", "shield", "programs", True, "Ctrl+Shift+F", "singleton",
                    "aws95.programs.findings:make_findings", ("rules", "security", "audit"), 40),
    ProgramManifest("activity", "Activity Log", "magnifier", "programs", True, "Ctrl+Shift+L", "multi",
                    "aws95.programs.activity:make_activity", ("cloudtrail", "who did what", "events"), 50),
    ProgramManifest("snapshots", "Snapshots", "camera", "programs", True, None, "singleton",
                    "aws95.programs.snapshots:make_snapshots", ("diff", "trend", "history"), 60),
    ProgramManifest("fleet", "Fleet", "grid", "programs", False, None, "singleton",
                    "aws95.programs.fleet:make_fleet", ("matrix", "accounts"), 70),
    ProgramManifest("insights", "AI Insights", "robot", "programs", False, None, "singleton",
                    "aws95.programs.insights:make_insights", ("bedrock", "ai"), 80),
    ProgramManifest("reports", "Reports", "folder", "programs", True, None, "singleton",
                    "aws95.programs.reports:make_reports", ("export", "markdown", "csv"), 90),
    ProgramManifest("display", "Display Properties", "display", "settings", False, None, "singleton",
                    "aws95.programs.display:make_display",
                    ("theme", "dark", "screensaver", "sounds", "wallpaper", "font"), 200),
    ProgramManifest("screensavers", "Screen Savers", "display", "accessories", False, None, "singleton",
                    "aws95.programs.screensavers:make_screensavers",
                    ("screensaver", "saver", "starfield", "pipes", "maze", "mystify", "bounce"), 210),
    ProgramManifest("recycle", "Recycle Bin", "recycle", "system", True, None, "singleton",
                    "aws95.programs.recycle_bin:make_recycle_bin", ("closed", "restore", "trash"), 300),
)
