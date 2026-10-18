"""Sign-in and Assistant steps, their pages, and the default registry."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton

from lightfall.agents.backends.info import AuthStatus
from lightfall.auth.session import Session, SessionManager, User
from lightfall.setup.bootstrap import Bootstrap, apply_bootstrap
from lightfall.setup.page import Tile
from lightfall.setup.step import StepState, StepStatus
from lightfall.setup.steps.assistant import AssistantStep
from lightfall.setup.steps.signin import SignInStep
from lightfall.ui.dialogs.login_dialog import LoginResult
from lightfall.ui.preferences.agent_backend_section import AgentBackendSection

KEYCLOAK = Bootstrap("http://s", {
    "schema": 1, "facility": {"id": "als", "name": "ALS"},
    "auth": {"provider": "keycloak", "server_url": "https://bcgkyclk.als.lbl.gov"},
})


class FakeLogin:
    result = LoginResult.AUTHENTICATED
    made: list[dict] = []

    def __init__(self, **kwargs):
        FakeLogin.made.append(kwargs)

    def exec(self):
        if FakeLogin.result is LoginResult.AUTHENTICATED:
            SessionManager.get_instance().attach_session(Session(user=User(username="ron")))
        return 0

    @property
    def login_result(self):
        return FakeLogin.result


@pytest.fixture
def fake_login(monkeypatch):
    from lightfall.setup.pages import signin as page_mod

    FakeLogin.made = []
    FakeLogin.result = LoginResult.AUTHENTICATED
    monkeypatch.setattr(page_mod, "LoginDialog", FakeLogin)
    return FakeLogin


class _SectionInfo:
    def __init__(self, kind, ok):
        self.kind = kind
        self._status = AuthStatus(ok, "Signed in" if ok else "Not signed in")

    def auth_status(self, refresh=False):
        return self._status

    def list_models(self, refresh=False):
        return []

    def sign_in_argv(self):
        return ["agy"]

    def sign_in_env(self):
        return {}


class _DetectInfo:
    def __init__(self, name, ok):
        self.display_name = name
        self._ok = ok

    def auth_status(self, refresh=False):
        return AuthStatus(self._ok, "")


def _section(stops=None):
    infos = {"agy": _SectionInfo("agy", True), "claude-code": _SectionInfo("claude-code", False)}

    class Counting(AgentBackendSection):
        def stop(self):
            if stops is not None:
                stops.append(1)
            super().stop()

    return Counting(info_factory=lambda kind, agy_path=None: infos[kind],
                    spawn=lambda *a, **k: None)


def _signin_page(qtbot, ctx):
    from lightfall.setup.pages.signin import SignInPage

    page = SignInPage(ctx)
    qtbot.addWidget(page)
    page.show()
    return page


def test_signin_detect_and_validate(ctx):
    step = SignInStep()
    assert step.detect(ctx) == StepState(StepStatus.NEEDS_INPUT, "Not signed in")
    assert step.validate(ctx)
    ctx.session.attach_session(Session(user=User(username="ron")))
    assert step.detect(ctx) == StepState(StepStatus.SATISFIED, "ron · Local account")
    assert step.validate(ctx) is None


def test_sign_in_opens_the_login_dialog_and_completes(qtbot, ctx, fake_login):
    page = _signin_page(qtbot, ctx)
    done = []
    page.completed.connect(lambda: done.append(1))
    assert page.primary_label() == "Sign in with your facility"
    assert page.skip_label() == "Continue as guest"
    page.on_primary()
    assert fake_login.made[0]["allow_guest"] is True
    assert done == [1]
    assert page.summary() == "ron · Local account"
    assert page.primary_label() == "Continue"


def test_guest_from_the_login_dialog_is_a_skip(qtbot, ctx, fake_login):
    fake_login.result = LoginResult.GUEST
    page = _signin_page(qtbot, ctx)
    skipped, done = [], []
    page.skip_requested.connect(lambda: skipped.append(1))
    page.completed.connect(lambda: done.append(1))
    page.on_primary()
    assert skipped == [1] and done == []


def test_cancelled_sign_in_stays_on_the_step(qtbot, ctx, fake_login):
    fake_login.result = LoginResult.CANCELLED
    page = _signin_page(qtbot, ctx)
    done = []
    page.completed.connect(lambda: done.append(1))
    page.on_primary()
    assert done == []
    assert page.note.kind() == "warn" and "cancelled" in page.note.text()
    assert not ctx.session.is_authenticated


def test_sso_card_names_the_keycloak_host(qtbot, ctx):
    apply_bootstrap(KEYCLOAK, ctx.config)
    ctx.bootstrap = KEYCLOAK
    page = _signin_page(qtbot, ctx)
    card = page.findChild(Tile, "setupSsoCard")
    assert card.title_label.text() == "ALS single sign-on"
    assert card.subtitle_label.text() == "Keycloak · bcgkyclk.als.lbl.gov · opens your browser"
    assert page.primary_label() == "Sign in with ALS"
    assert page.note.kind() == "ok" and "sign-on" in page.note.text()


def test_assistant_detect(ctx):
    ok = AssistantStep(info_factory=lambda: _DetectInfo("Gemini", True))
    assert ok.detect(ctx) == StepState(StepStatus.SATISFIED, "Gemini · signed in")
    no = AssistantStep(info_factory=lambda: _DetectInfo("Claude", False))
    assert no.detect(ctx) == StepState(StepStatus.NEEDS_INPUT, "Claude · not signed in")


def test_assistant_page_defaults_to_the_bootstrap_backend(qtbot, ctx):
    from lightfall.setup.pages.assistant import AssistantPage

    apply_bootstrap(Bootstrap("http://s", {"schema": 1, "agent": {"backend": "agy"}}),
                    ctx.config)
    page = AssistantPage(ctx, _section())
    qtbot.addWidget(page)
    page.show()
    assert page.findChild(QPushButton, "setupBackend-agy").isChecked()
    assert page.section.selected_kind() == "agy"
    qtbot.waitUntil(lambda: page.summary() == "Gemini · signed in", timeout=3000)


def test_wizard_embeds_saves_and_stops_the_assistant_section(qtbot, ctx):
    from lightfall.setup.registry import SetupRegistry
    from lightfall.setup.wizard import SetupWizard

    stops = []
    step = AssistantStep(info_factory=lambda: _DetectInfo("Claude", False),
                         section_factory=lambda: _section(stops))
    registry = SetupRegistry()
    registry.register(step)
    wizard = SetupWizard(None, ctx, registry)
    qtbot.addWidget(wizard)
    wizard.show()
    qtbot.waitUntil(lambda: wizard.current_step_id() == "assistant", timeout=3000)
    wizard.findChild(QPushButton, "setupBackend-agy").click()
    stops.clear()
    wizard.findChild(QPushButton, "setupPrimary").click()
    assert ctx.config.get("preferences.agent_backend") == "agy"
    assert wizard.current_step_id() == "ready"
    wizard.reject()
    assert stops


def test_default_registry_order():
    from lightfall.setup.registry import default_registry

    assert [s.id for s in default_registry().steps()] == [
        "facility", "network", "signin", "assistant"]
    assert [s.title for s in default_registry().steps()] == [
        "Facility", "Network", "Sign in", "Assistant"]


def test_signin_not_satisfied_by_a_session_from_another_provider(ctx):
    from lightfall.auth.providers.local import LocalAuthProvider

    apply_bootstrap(KEYCLOAK, ctx.config)
    ctx.session.set_provider(LocalAuthProvider())
    ctx.session.attach_session(Session(user=User(username="ron")))
    step = SignInStep()
    assert step.detect(ctx).status is StepStatus.NEEDS_INPUT


def test_assistant_page_rechecks_after_leave_and_reenter(qtbot, ctx):
    import threading

    from PySide6.QtWidgets import QLabel

    from lightfall.setup.pages.assistant import AssistantPage

    gate = threading.Event()
    gate.set()  # the first (construction-time) check returns at once
    stops = []

    class GatedInfo(_SectionInfo):
        def auth_status(self, refresh=False):
            gate.wait(5)
            return self._status

    infos = {"agy": GatedInfo("agy", True), "claude-code": GatedInfo("claude-code", True)}

    class Counting(AgentBackendSection):
        def stop(self):
            stops.append(1)
            super().stop()

    section = Counting(info_factory=lambda kind, agy_path=None: infos[kind],
                       spawn=lambda *a, **k: None)
    page = AssistantPage(ctx, section)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: section.last_status is not None, timeout=3000)
    gate.clear()
    page.on_enter()  # starts a check that blocks on the gate
    assert section.last_status is None
    assert page.summary().endswith("checking…")  # neutral while in flight
    stops.clear()
    page.on_leave()
    assert stops  # stopped on leave; the in-flight result is dropped
    page.on_enter()  # re-enter: a fresh check starts
    gate.set()
    status = page.findChild(QLabel, "assistantBackendStatus")
    qtbot.waitUntil(lambda: status.text().startswith("✓"), timeout=3000)
    assert page.summary() == "Claude · signed in"


def test_signin_provider_mismatch_asks_for_the_new_provider(qtbot, ctx):
    from lightfall.auth.providers.local import LocalAuthProvider

    apply_bootstrap(KEYCLOAK, ctx.config)
    ctx.bootstrap = KEYCLOAK
    ctx.session.set_provider(LocalAuthProvider())
    ctx.session.attach_session(Session(user=User(username="ron")))
    step = SignInStep()
    assert step.validate(ctx) == "You're not signed in yet."
    page = _signin_page(qtbot, ctx)
    assert page.primary_label() == "Sign in with ALS"
    assert page.summary() == ""
    assert page.note.kind() == "warn"
    assert "Local account" in page.note.text() and "Keycloak" in page.note.text()
    assert page.findChild(Tile, "setupSsoCard").title_label.text() == "ALS single sign-on"


class OtherProviderLogin(FakeLogin):
    """LoginDialog offers every registered provider: the user picks a non-configured one."""

    def exec(self):
        from lightfall.auth.providers.local import LocalAuthProvider

        manager = SessionManager.get_instance()
        manager.set_provider(LocalAuthProvider())
        manager.attach_session(Session(user=User(username="ron")))
        return 0


def test_signin_in_this_run_with_another_offered_provider_counts(qtbot, ctx, fake_login,
                                                                  monkeypatch):
    from lightfall.setup.pages import signin as page_mod

    monkeypatch.setattr(page_mod, "LoginDialog", OtherProviderLogin)
    apply_bootstrap(KEYCLOAK, ctx.config)
    ctx.bootstrap = KEYCLOAK
    step = SignInStep()
    page = _signin_page(qtbot, ctx)
    done = []
    page.completed.connect(lambda: done.append(1))
    page.on_primary()
    assert done == [1]
    assert step.validate(ctx) is None
    assert step.detect(ctx) == StepState(StepStatus.SATISFIED, "ron · Local account")
    assert page.primary_label() == "Continue"
    assert page.note.kind() != "warn"
    card = page.findChild(Tile, "setupSsoCard")
    assert card.title_label.text() == "Signed in as ron"
    assert card.subtitle_label.text() == "Local account"


def test_a_later_session_from_another_provider_needs_sign_in_again(qtbot, ctx, fake_login,
                                                                    monkeypatch):
    from lightfall.auth.providers.local import LocalAuthProvider
    from lightfall.setup.pages import signin as page_mod

    monkeypatch.setattr(page_mod, "LoginDialog", OtherProviderLogin)
    apply_bootstrap(KEYCLOAK, ctx.config)
    page = _signin_page(qtbot, ctx)
    page.on_primary()
    assert SignInStep().validate(ctx) is None
    # A different session (not the one this run's sign-in made) gets no pass.
    ctx.session.set_provider(LocalAuthProvider())
    ctx.session.attach_session(Session(user=User(username="mallory")))
    assert SignInStep().validate(ctx) == "You're not signed in yet."


def test_session_manager_exposes_its_provider_read_only(ctx):
    from lightfall.auth.providers.local import LocalAuthProvider

    assert ctx.session.provider is None
    provider = LocalAuthProvider()
    ctx.session.set_provider(provider)
    assert ctx.session.provider is provider
    with pytest.raises(AttributeError):
        ctx.session.provider = None
