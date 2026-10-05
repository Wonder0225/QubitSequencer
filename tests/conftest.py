"""GUI verification must never write to the user's Windows settings."""

import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def isolated_window_settings(monkeypatch, tmp_path):
    from qubit_sequencer import gui
    settings = QSettings(str(tmp_path / "application.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    monkeypatch.setattr(gui, "QSettings", lambda *_args, **_kwargs: settings)
