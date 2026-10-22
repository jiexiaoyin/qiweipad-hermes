"""插件注册：平台面（``ctx.register_platform``）+ 交互式配置。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from gateway.platforms._shared import (
    env_is_connected as _env_is_connected,
    get_scoped_secret as _get_scoped_secret,
    seed_extra_from_env as _seed_extra_from_env,
)

from .adapter import PLATFORM_NAME, QWPadAdapter
from .client import DEFAULT_BASE_URL
from .outbound import standalone_send

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _as_bool(raw: str) -> bool:
    text = str(raw).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def _as_int(raw: str) -> int:
    return int(str(raw).strip())


def check_requirements() -> bool:
    """被动探针：只判断 TOKEN 是否配置，绝不安装/联网。"""
    return bool((_get_scoped_secret("QWPAD_TOKEN") or "").strip())


def validate_config(config: Any) -> bool:
    extra = getattr(config, "extra", {}) or {}
    token = (_get_scoped_secret("QWPAD_TOKEN") or extra.get("token")
             or getattr(config, "token", "") or "")
    return bool(str(token).strip())


is_connected = _env_is_connected("QWPAD_TOKEN")


def _plugin_profile_home() -> Optional[Path]:
    """插件所在 profile 的 HERMES_HOME。

    profile 级副本路径是 ``profiles/<name>/plugins/qwpad/registration.py``，
    直接从 ``__file__`` 反推比依赖 secret scope 更可靠；home 级副本才回退到
    ``get_hermes_home()``。
    """
    parents = Path(__file__).resolve().parents
    if len(parents) > 3 and parents[1].name == "plugins" and parents[3].name == "profiles":
        return parents[2]
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return None


def _profile_home_channel(home: Path) -> str:
    """只读本 profile 的 home channel：先 ``.env``（快），再完整 secret scope。"""
    try:
        from agent.secret_scope import load_env_file
        value = (load_env_file(home / ".env").get("QWPAD_HOME_CHANNEL") or "").strip()
    except Exception:
        value = ""
    if value:
        return value
    try:
        from agent.secret_scope import build_profile_secret_scope
        return (build_profile_secret_scope(home).get("QWPAD_HOME_CHANNEL") or "").strip()
    except Exception:
        return ""


def _export_home_channel_fallback() -> None:
    """把本 profile 的 home channel 放进进程环境。

    网关的「No home channel」检查在尚未进入 profile secret scope 时会回退到
    ``os.getenv("QWPAD_HOME_CHANNEL")``；profile 的 ``.env`` 不会自动导出到进程环境，
    所以每个新会话都会重复弹提示。这里在插件注册/配置加载时补一次，值只来自本 profile。
    """
    if os.environ.get("QWPAD_HOME_CHANNEL"):
        return
    try:
        home = (_get_scoped_secret("QWPAD_HOME_CHANNEL") or "").strip()
        if not home:
            profile_home = _plugin_profile_home()
            if profile_home is not None:
                home = _profile_home_channel(profile_home)
        if home:
            os.environ["QWPAD_HOME_CHANNEL"] = home
            logger.info("[QWPad] 已导出 QWPAD_HOME_CHANNEL 进程回退值")
    except Exception:
        logger.debug("[QWPad] 导出 QWPAD_HOME_CHANNEL 失败", exc_info=True)


def _env_enablement() -> Optional[dict]:
    """env-only 配置在 ``hermes gateway status`` 里也要显示成已配置。"""
    token = (_get_scoped_secret("QWPAD_TOKEN") or "").strip()
    if not token:
        return None
    _export_home_channel_fallback()
    seed = _seed_extra_from_env((
        ("QWPAD_API_BASE", "api_base", None),
        ("QWPAD_POLL_WAIT_SECONDS", "poll_wait_seconds", _as_int),
        ("QWPAD_MAX_MESSAGE_LENGTH", "max_message_length", _as_int),
        ("QWPAD_GROUP_REQUIRE_MENTION", "require_mention", _as_bool),
        ("QWPAD_QUOTE_REPLIES", "quote_replies", _as_bool),
    ), home_env="QWPAD_HOME_CHANNEL")
    return {"token": token, **seed}


def interactive_setup() -> None:
    """``hermes gateway setup`` 里的 QWPad 流程。"""
    from hermes_cli.setup import (
        get_env_value, print_header, print_info, print_success, print_warning,
        prompt, prompt_yes_no, save_env_value,
    )
    from hermes_cli.setup_platforms import declines_reconfigure

    print_header("QWPad（企业微信 pad 协议）")
    if declines_reconfigure("QWPad", "Reconfigure QWPad?", "QWPAD_TOKEN"):
        return
    print_info("一个 QWPad TOKEN 对应一个企业微信账号，消息以真人客户端身份收发。",
               "TOKEN 在 QWPad 工作台创建（wxa_ 前缀），并在那边完成扫码登录。")
    token = prompt("QWPad TOKEN (wxa_...)", password=True,
                   default=get_env_value("QWPAD_TOKEN") or "")
    if not token or not str(token).strip():
        print_warning("TOKEN 必填 —— 跳过 QWPad 配置")
        return
    save_env_value("QWPAD_TOKEN", str(token).strip())
    base = prompt("API 地址", default=get_env_value("QWPAD_API_BASE") or DEFAULT_BASE_URL)
    if base and base.strip() != DEFAULT_BASE_URL:
        save_env_value("QWPAD_API_BASE", base.strip())
    if prompt_yes_no("群里必须 @ 机器人才回复？（推荐）", True):
        save_env_value("QWPAD_GROUP_REQUIRE_MENTION", "true")
    else:
        save_env_value("QWPAD_GROUP_REQUIRE_MENTION", "false")
    home = prompt("cron/通知默认发送目标（群或成员 ID，可空）",
                  default=get_env_value("QWPAD_HOME_CHANNEL") or "")
    if home and home.strip():
        save_env_value("QWPAD_HOME_CHANNEL", home.strip())
    print_success("QWPad 配置已写入 ~/.hermes/.env")
    print_info("重启网关生效：hermes gateway restart")


def register(ctx: Any) -> None:
    _export_home_channel_fallback()
    ctx.register_platform(
        name=PLATFORM_NAME,
        label="QWPad (企业微信 pad 协议)",
        dashboard_description="以企业微信真人客户端身份收发消息（pad 协议，非 bot / 自建应用）。",
        adapter_factory=QWPadAdapter,
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["QWPAD_TOKEN"],
        install_hint="在 ~/.hermes/.env 写入 QWPAD_TOKEN（wxa_ 前缀），或跑 `hermes gateway setup`。",
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="QWPAD_HOME_CHANNEL",
        standalone_sender_fn=standalone_send,
        allowed_users_env="QWPAD_ALLOWED_USERS",
        allow_all_env="QWPAD_ALLOW_ALL_USERS",
        max_message_length=2000,
        emoji="💼",
        display_tier="low",
        allow_update_command=True,
        platform_hint=(
            "You are chatting via QWPad (企业微信 / WeCom desktop pad protocol) as a real "
            "user account, not a bot. Plain text only — markdown is stripped. "
            "Group chats require an @mention to trigger a reply; reply naturally and briefly."
        ),
    )
    logger.info("[QWPad] 平台已注册: %s", PLATFORM_NAME)
