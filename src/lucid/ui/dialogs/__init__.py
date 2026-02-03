"""UI dialogs for LUCID.

This module provides modal dialogs for the application.
"""

from lucid.ui.dialogs.about_dialog import AboutDialog, show_about_dialog
from lucid.ui.dialogs.create_plan_dialog import CreatePlanDialog
from lucid.ui.dialogs.login_dialog import LoginDialog
from lucid.ui.dialogs.oauth_browser_dialog import OAuthBrowserDialog

__all__ = [
    "AboutDialog",
    "CreatePlanDialog",
    "LoginDialog",
    "OAuthBrowserDialog",
    "show_about_dialog",
]
