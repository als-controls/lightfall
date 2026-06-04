"""UI dialogs for Lightfall.

This module provides modal dialogs for the application.
"""

from lightfall.ui.dialogs.about_dialog import AboutDialog, show_about_dialog
from lightfall.ui.dialogs.base import LFDialog
from lightfall.ui.dialogs.bug_report_dialog import BugReportDialog, report_bug
from lightfall.ui.dialogs.create_plan_dialog import CreatePlanDialog
from lightfall.ui.dialogs.go_to_position_dialog import GoToPositionDialog
from lightfall.ui.dialogs.login_dialog import LoginDialog
from lightfall.ui.dialogs.oauth_browser_dialog import OAuthBrowserDialog
from lightfall.ui.dialogs.sample_metadata_dialog import SampleMetadataDialog

__all__ = [
    "AboutDialog",
    "BugReportDialog",
    "CreatePlanDialog",
    "GoToPositionDialog",
    "LoginDialog",
    "LFDialog",
    "OAuthBrowserDialog",
    "SampleMetadataDialog",
    "report_bug",
    "show_about_dialog",
]
