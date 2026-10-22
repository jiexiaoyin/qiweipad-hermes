"""home channel 进程环境回退：profile .env 必须能被插件主动导出。"""

from __future__ import annotations

import os
from pathlib import Path

import agent.secret_scope as secret_scope

from qwpad import registration

HOME_ID = "7881300000000001"


def _profile_tree(tmp_path: Path, *, value: str | None = HOME_ID) -> Path:
    home = tmp_path / "profiles" / "qwpad-test"
    plugin = home / "plugins" / "qwpad"
    plugin.mkdir(parents=True)
    lines = ["QWPAD_TOKEN=wxa_test"]
    if value is not None:
        lines.append(f"QWPAD_HOME_CHANNEL={value}")
    (home / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plugin / "registration.py"


def _no_scope(monkeypatch) -> None:
    monkeypatch.setattr(registration, "_get_scoped_secret",
                        lambda name, default=None: None)
    monkeypatch.setattr(secret_scope, "build_profile_secret_scope", lambda home: {})


def test_exports_home_channel_from_profile_env(tmp_path, monkeypatch):
    fake_file = _profile_tree(tmp_path)
    monkeypatch.setattr(registration, "__file__", str(fake_file))
    monkeypatch.delenv("QWPAD_HOME_CHANNEL", raising=False)
    _no_scope(monkeypatch)

    registration._export_home_channel_fallback()

    assert os.environ["QWPAD_HOME_CHANNEL"] == HOME_ID


def test_existing_process_value_is_not_overwritten(tmp_path, monkeypatch):
    fake_file = _profile_tree(tmp_path, value="111")
    monkeypatch.setattr(registration, "__file__", str(fake_file))
    monkeypatch.setenv("QWPAD_HOME_CHANNEL", "999")
    _no_scope(monkeypatch)

    registration._export_home_channel_fallback()

    assert os.environ["QWPAD_HOME_CHANNEL"] == "999"


def test_missing_home_channel_does_not_export(tmp_path, monkeypatch):
    fake_file = _profile_tree(tmp_path, value=None)
    monkeypatch.setattr(registration, "__file__", str(fake_file))
    monkeypatch.delenv("QWPAD_HOME_CHANNEL", raising=False)
    _no_scope(monkeypatch)

    registration._export_home_channel_fallback()

    assert "QWPAD_HOME_CHANNEL" not in os.environ


def test_home_scope_copy_falls_back_to_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    plugin = home / "plugins" / "qwpad"
    plugin.mkdir(parents=True)
    (home / ".env").write_text(f"QWPAD_HOME_CHANNEL={HOME_ID}\n", encoding="utf-8")
    monkeypatch.setattr(registration, "__file__", str(plugin / "registration.py"))
    monkeypatch.delenv("QWPAD_HOME_CHANNEL", raising=False)
    _no_scope(monkeypatch)

    import hermes_constants
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)

    registration._export_home_channel_fallback()

    assert os.environ["QWPAD_HOME_CHANNEL"] == HOME_ID
