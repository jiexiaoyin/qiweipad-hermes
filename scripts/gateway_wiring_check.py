#!/usr/bin/env python3
"""在指定 HERMES_HOME 里验证「插件加载 → 网关配置 → 适配器构造」这一段接线。

    HERMES_HOME=/path/to/.testhome \
      .venv/bin/python scripts/gateway_wiring_check.py

只做发现与构造，不 connect()（那一步由 live_probe.py 负责）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_TREE = Path(os.environ.get("HERMES_TREE", HERMES_HOME / "hermes-agent"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERMES_TREE))


def main() -> int:
    from hermes_cli.env_loader import load_hermes_dotenv
    from hermes_cli.plugins import discover_plugins
    from gateway.config import load_gateway_config
    from gateway.platform_registry import platform_registry

    # 真实网关启动时也走这一步（把 profile 的 .env 灌进进程环境）
    load_hermes_dotenv()
    discover_plugins(force=True)
    print(f"HERMES_HOME={os.environ.get('HERMES_HOME')}")
    print(f"is_registered(qwpad) = {platform_registry.is_registered('qwpad')}")

    gateway = load_gateway_config()
    platforms = getattr(gateway, "platforms", {}) or {}
    print(f"配置里的平台: {sorted(str(getattr(k, 'value', k)) for k in platforms)}")
    from gateway.config import Platform
    pconfig = platforms.get(Platform("qwpad")) or platforms.get("qwpad")
    if pconfig is None:
        print("✗ 网关配置里没有 qwpad（env_enablement_fn 没生效？）")
        return 1
    print(f"qwpad.enabled = {pconfig.enabled}")
    print(f"qwpad.extra = {json.dumps({k: ('***' if 'token' in k else v) for k, v in (pconfig.extra or {}).items()}, ensure_ascii=False)}")
    print(f"qwpad.home_channel = {getattr(pconfig, 'home_channel', None)}")

    adapter = platform_registry.create_adapter("qwpad", pconfig)
    if adapter is None:
        print("✗ create_adapter 返回 None")
        return 1
    print(f"✓ adapter = {type(adapter).__name__} platform={adapter.platform.value} "
          f"max_len={adapter.max_message_length} require_mention={adapter.require_mention}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
