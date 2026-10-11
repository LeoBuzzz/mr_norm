from __future__ import annotations

from mr_norm.apps.service_bot import command_runs_module


def test_command_runs_expected_module() -> None:
    assert command_runs_module(
        ["python.exe", "-u", "-m", "mr_norm.apps.telegram_bot"],
        "mr_norm.apps.telegram_bot",
    )


def test_command_does_not_match_other_bot() -> None:
    assert not command_runs_module(
        ["python.exe", "-u", "-m", "mr_norm.apps.vk_bot"],
        "mr_norm.apps.telegram_bot",
    )


def test_command_requires_module_switch() -> None:
    assert not command_runs_module(
        ["python.exe", "mr_norm.apps.vk_bot"],
        "mr_norm.apps.vk_bot",
    )
