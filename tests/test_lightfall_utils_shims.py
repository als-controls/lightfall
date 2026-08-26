"""Every pre-extraction import path must keep working and resolve to lightfall_utils."""


def test_logging_shim():
    from lightfall.utils.logging import configure_logging, log_time, logger  # noqa: F401
    from lightfall_utils.logging import configure_logging as new_configure

    assert configure_logging is new_configure


def test_log_buffer_shim():
    from lightfall.utils.log_buffer import LogBuffer
    from lightfall_utils.log_buffer import LogBuffer as NewLogBuffer

    assert LogBuffer is NewLogBuffer


def test_threads_shim():
    from lightfall.utils.threads import (
        QThreadFuture,
        get_thread_manager,
        initialize_main_thread_invoker,  # noqa: F401  (monkeypatched by other tests)
        invoke_in_main_thread,  # noqa: F401
        thread_manager,
    )
    from lightfall_utils.threads import QThreadFuture as NewQThreadFuture

    assert QThreadFuture is NewQThreadFuture
    assert get_thread_manager() is thread_manager


def test_caproto_shutdown_shim():
    from lightfall.utils.caproto_shutdown import drain_callback_executors
    from lightfall_utils.caproto_shutdown import drain_callback_executors as new_drain

    assert drain_callback_executors is new_drain


def test_config_shims():
    from lightfall.config import ConfigManager, ConfigPriority, LayeredConfig  # noqa: F401
    from lightfall.config.manager import ConfigManager as DeepConfigManager
    from lightfall_utils.config import ConfigManager as BaseConfigManager

    assert ConfigManager is DeepConfigManager
    assert issubclass(ConfigManager, BaseConfigManager)


def test_config_manager_defaults_to_lfconfig():
    from lightfall.config import ConfigManager
    from lightfall.config.schema import LFConfig

    mgr = ConfigManager(skip_standard_paths=True)
    assert isinstance(mgr.model, LFConfig)


def test_theme_shims():
    from lightfall.plugins.theme_plugin import ThemeDefinition, ThemePlugin
    from lightfall.ui.theme import Theme, ThemeManager, ThemeRegistry, scaled_pt  # noqa: F401
    from lightfall.ui.theme.manager import LIGHT_COLORS  # noqa: F401  (deep import used by tests)
    from lightfall_utils.theming import ThemeDefinition as NewDefinition, ThemeProvider

    assert ThemeDefinition is NewDefinition
    assert issubclass(ThemePlugin, ThemeProvider)


def test_builtin_theme_shims_satisfy_loader_contract():
    from lightfall.plugins.theme_plugin import ThemePlugin
    from lightfall.ui.theme.builtin import LightThemePlugin, generate_islands_stylesheet  # noqa: F401

    assert issubclass(LightThemePlugin, ThemePlugin)
    theme = LightThemePlugin()
    assert theme.get_theme_definition() is not None


def test_docking_contributor_registered(qtbot):
    import lightfall.ui.theme  # noqa: F401  (import registers the contributor)
    from lightfall.ui.docking.theme import generate_docking_stylesheet
    from lightfall_utils.theming import ThemeManager

    assert generate_docking_stylesheet in ThemeManager.default_stylesheet_contributors

    # End-to-end: the registered contributor's output must actually land in
    # the generated stylesheet, not just be present in the registry.
    css = ThemeManager.get_instance().generate_stylesheet()
    assert "#PanelTitleBar" in css


def test_ca_shims():
    from lightfall.epics.ca import PV, SharedContext
    from lightfall.epics.ca.pv import PV as DeepPV
    from lightfall_utils.ca import PV as NewPV, SharedContext as NewSharedContext

    assert PV is NewPV is DeepPV
    assert SharedContext is NewSharedContext


def test_crash_diagnostics_reexports_affinity():
    from lightfall.utils.crash_diagnostics import gui_thread_only
    from lightfall_utils.qt_affinity import gui_thread_only as new_gui_thread_only

    assert gui_thread_only is new_gui_thread_only
