"""QWPad 平台插件（企业微信 pad 协议）。

Hermes 只要求一个入口：``register(ctx)``。
"""

from .registration import register

__all__ = ["register"]
