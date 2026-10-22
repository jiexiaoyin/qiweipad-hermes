# qiweipad-hermes

> **QWPad 企业微信 pad 协议 · Hermes 平台适配器**
> 让 Agent 以「真人企业微信客户端」的身份收发消息，而不是一个机器人。

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
![Hermes](https://img.shields.io/badge/Hermes-platform%20plugin-6b4fbb)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.10-informational)](./CHANGELOG.md)

---

## 项目简介

`qiweipad-hermes` 是一个 Hermes 平台插件，通过
[QWPad](https://api.qwpad.com) 的 `wxa_` TOKEN 接入企业微信 **pad 协议**：

- **不是 bot**：不需要 corpid / secret / 自建应用 / 可信域名 / 回调地址；
- **一个 TOKEN = 一个企业微信账号**，收发都以该账号的真实客户端身份完成；
- **纯插件实现**：通过 `ctx.register_platform` 注册，不改 Hermes 核心代码；
- 支持私聊、群聊（默认必须 @）、图片、语音、文件、视频、位置、链接、聊天记录、
  名片、小程序、视频号、通知卡片等常见消息；
- 自带事件游标、去重、媒体缓存、非聊天事件旁路留存、引用回复、cron 主动发送。

> 仓库名是 `qiweipad-hermes`，Hermes 插件目录名和 Python 包名是 **`qwpad`**。
> 部署时请把仓库内容放到 `plugins/qwpad/`，不要放成 `plugins/qiweipad-hermes/`。

## 功能特性

### 入站

| 能力 | 说明 |
|---|---|
| 文本 / 富文本 | `kText` / `kRichText`，自动剥离 HTML 片段 |
| 图片 | 企业微信 CDN 图片、个人微信 `kWechatImage`、表情 |
| 语音 | 下载 SILK → 解码 WAV → 交给 Hermes STT 转写 |
| 视频 / 文件 | 下载到媒体缓存，保留文件名 |
| 位置 / 链接 / 名片 / 小程序 / 视频号 | 转成可读文本，交给模型 |
| 聊天记录 `kForward` | 渲染发送人与子消息；**内嵌图片/文件会一起下载**作为附件 |
| 引用消息 | 结构化 `reply` 优先，兼容客户端压平后的 `"原文"\n------\n新内容` |
| 群消息 | 默认必须 @ 机器人；@ 前缀自动剥离 |
| 非聊天事件 | 好友申请、审批、抄送、应用卡片等只留存、不回复 |

### 出站

| 能力 | 状态 |
|---|---|
| 文本 / 长文本分片 | ✅ 插件已实现 |
| 引用回复 | ✅ `sendHyperText` 两段式 + `reply`，客户端显示引用气泡与正文 |
| 图片 | ✅ `sendImage` |
| 文件 / 文档 | ✅ `sendFile` |
| 语音气泡 | ✅ ffmpeg → SILK → `sendVoice`，失败降级为文件 |
| 视频 | ⚠️ 当前按文件发送；原生 `sendVideo` 已验证可用但未接线 |
| 链接 / 位置 / 名片 | 🔬 API 已验证，插件暂未暴露 |
| 图文（文字+图片） | 🔬 API 已验证（图片子类型必须为 14） |
| 大文件 / 大视频 | 🔬 API 已验证（大文件需用 `fileConvertId`） |
| 聊天记录 | ❌ `sendChatRecord` 对个人微信和企业微信自己都返回 502 |

### 工程能力

- **事件游标落盘**：按 profile + TOKEN 指纹保存，网关重启不重放；
- **去重集合**：避免同一条消息被重复处理；
- **媒体缓存**：图片/语音/视频/文件按内容落盘，模型可直接读取；
- **非聊天事件旁路留存**：0600 JSONL、10MB × 5 轮转、默认 30 天；
- **profile 隔离**：多账号 = 多 profile，各配各的 `QWPAD_TOKEN`；
- **cron / 主动通知**：通过 `QWPAD_HOME_CHANNEL` 指定默认发送目标；
- **真实主路径探针**：连接、收、发、插件发现、网关接线都有脚本可跑。

## 架构

```
企业微信 / 个人微信
        │
        │  QWPad pad 协议
        ▼
  QWPad API (api.qwpad.com)
        │
        │  /events long-poll          /msg/send*
        ▼                               ▲
┌──────────────────────────────────────┴──────────┐
│                Hermes Gateway                    │
│                                                  │
│  qwpad 插件                                       │
│  ├─ adapter.py    连接 / 轮询 / 去重 / 分发 / 发送 │
│  ├─ inbound.py    事件 → InboundMessage（纯函数）  │
│  ├─ outbound.py   清洗 / 分片 / 上传 / 引用 / 语音 │
│  ├─ media.py      下载 / SILK 解码 / 编码 / 缓存   │
│  ├─ retained.py   非聊天事件分流与旁路留存          │
│  └─ state.py      游标 + 去重集合                  │
│                                                  │
│  Hermes Agent（对话、工具、cron、技能）            │
└──────────────────────────────────────────────────┘
```

## 目录结构

```
qiweipad-hermes/
├── qwpad/                     # Python 包（生产目录名也是 qwpad）
│   ├── __init__.py            # register(ctx) 入口
│   ├── registration.py        # 平台注册 + gateway setup
│   ├── adapter.py             # BasePlatformAdapter 实现
│   ├── client.py              # QWPad HTTP 客户端 + msgType 常量
│   ├── inbound.py             # 事件解析（纯函数，可单测）
│   ├── outbound.py            # 文本/媒体/语音/引用发送
│   ├── media.py               # 媒体下载 + SILK 编解码 + 缓存
│   ├── retained.py            # 非聊天事件旁路留存
│   └── state.py               # 游标 + 去重状态
├── plugin.yaml                # Hermes 插件清单
├── tests/                     # 单元测试 + 可选 LIVE 冒烟
├── scripts/
│   ├── live_probe.py          # 真实 TOKEN 连接/收/发探针
│   └── gateway_wiring_check.py# 插件发现 → 网关配置 → 适配器构造
├── docs/api-notes.md          # 实测 API 事实与坑
├── sync.sh                    # 同步到生产插件目录（备份 + 校验）
├── CHANGELOG.md
└── README.md
```

## 环境要求

- Python **3.12+**（与 Hermes 运行环境一致）；
- 已安装并运行的 Hermes Gateway；
- 一个 QWPad `wxa_` 业务 TOKEN（一个 TOKEN 对应一个企业微信账号）；
- 可选：`ffmpeg` + SILK 编码器（发送语音气泡）、SILK 解码器（接收语音转写）。

## 快速开始

### 1. 获取 TOKEN

在 QWPad 控制台创建一个 `wxa_` 业务 TOKEN，并确保对应企业微信账号在线。

### 2. 安装插件

```bash
git clone git@github.com:jiexiaoyin/qiweipad-hermes.git
cd qiweipad-hermes

# 注意目标目录名必须是 qwpad；只同步运行必需的包和清单
mkdir -p "$HERMES_HOME/plugins/qwpad"
rsync -a qwpad plugin.yaml "$HERMES_HOME/plugins/qwpad/"
```

如果使用 profile 级插件（会覆盖 home 级同名插件）：

```bash
mkdir -p "$HERMES_HOME/profiles/<profile>/plugins/qwpad"
rsync -a qwpad plugin.yaml "$HERMES_HOME/profiles/<profile>/plugins/qwpad/"
```

> ⚠️ profile 级副本优先级更高。修改代码后如果行为没变，先确认是不是改了 home 级、
> 但实际加载的是 `profiles/<name>/plugins/qwpad`。

### 3. 配置

写入 `$HERMES_HOME/.env`（home 级）或 `$HERMES_HOME/profiles/<profile>/.env`：

```dotenv
QWPAD_TOKEN=wxa_xxx
QWPAD_API_BASE=https://api.qwpad.com
QWPAD_HOME_CHANNEL=<你的 userId 或群 roomId>
```

建议权限：

```bash
chmod 600 "$HERMES_HOME/.env"
```

配置只走环境变量，不写 `config.yaml`。插件注册时会通过 `env_enablement_fn`
把 `QWPAD_*` 注入 `PlatformConfig.extra`，所以 `hermes gateway status` 能看到平台已配置。

### 4. 启用并重启

```bash
hermes plugins enable qwpad
hermes -p default gateway restart
```

> 多 profile 共用同一个 host gateway 时，必须重启 host gateway（通常是 `default`）；
> 单独重启某个 profile 会被拒绝。重启后检查日志：
>
> ```bash
> journalctl --user -u hermes-gateway -n 50 --no-pager
> ```

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `QWPAD_TOKEN` | — | **必填**，`wxa_` 业务 TOKEN |
| `QWPAD_API_BASE` | `https://api.qwpad.com` | API 地址 |
| `QWPAD_ALLOWED_USERS` | 空 | 允许对话的成员 ID，逗号分隔；空 = 只允许配对用户 |
| `QWPAD_ALLOW_ALL_USERS` | `false` | 放开所有人（仅调试；仍不会回复非 chat 事件） |
| `QWPAD_HOME_CHANNEL` | 空 | cron / 通知默认目标（userId 或 roomId） |
| `QWPAD_GROUP_REQUIRE_MENTION` | `true` | 群里必须 @ 机器人才回复 |
| `QWPAD_POLL_WAIT_SECONDS` | `25` | `/events` long-poll 等待秒数（1–30） |
| `QWPAD_MAX_MESSAGE_LENGTH` | `2000` | 单条文本字符上限，超出自动分片 |
| `QWPAD_QUOTE_REPLIES` | `true` | 文本回复自动引用本轮用户消息 |
| `QWPAD_RETAIN_EVENTS` | `friend_apply,friend_change,approval,cc,relationship,app_card,account_meta` | 非聊天事件留存类；`off` 关闭，`all` 全留 |
| `QWPAD_RETAIN_MAX_BYTES` | `10485760` | 旁路日志单文件上限 |
| `QWPAD_RETAIN_KEEP` | `5` | 旁路日志轮转保留份数 |
| `QWPAD_RETAIN_DAYS` | `30` | 旁路日志保留天数（0 = 不清理） |
| `QWPAD_SILK_DECODER` | `~/silk_decoder/silk/decoder` | 入站语音 SILK 解码器 |
| `QWPAD_SILK_ENCODER` | `~/silk_decoder/silk/encoder` | 出站语音 SILK 编码器 |
| `QWPAD_FFMPEG` | `ffmpeg` | ffmpeg 可执行文件路径 |

## 使用说明

### 私聊与群聊

- 私聊：直接给绑定的企业微信账号发消息即可。
- 群聊：默认必须 `@机器人`；可以通过 `QWPAD_GROUP_REQUIRE_MENTION=false` 关闭。
- **只有 `display.category=chat` 且外层事件类型为 `15000`（或缺失）的消息会进入对话**。
  系统通知、账号状态、待解析通知即使 `QWPAD_ALLOW_ALL_USERS=1` 也不会触发回复。
- `kAppTextCard`（38）属于应用通知：只留存，不回复。

### 发送消息与媒体

对 Agent 说“把这张图/这个文件发给我”即可。适配器会根据扩展名选择发送方式：

- 图片 → `sendImage`；
- 其他文件 → `sendFile`；
- 语音 → 先尝试 SILK 编码，走 `sendVoice` 生成语音气泡；编码器不可用时降级为文件；
- 视频当前按文件发送（原生视频接口已验证，但插件尚未接线）。

### cron / 主动通知

把 `QWPAD_HOME_CHANNEL` 设成默认目标后，Hermes 的 cron 或主动消息可以用
`deliver=qwpad` 发到该会话。目标可以是个人 userId，也可以是群 roomId。

### 探针脚本

```bash
# 真实 TOKEN：连接 / 收 / 发
QWPAD_TOKEN=wxa_xxx PYTHONPATH=... .venv/bin/python scripts/live_probe.py \
  --target <你的 userId> --listen 60

# 接线检查：插件发现 → 网关配置 → 适配器构造
HERMES_HOME=/path/to/hermes PYTHONPATH=... \
  .venv/bin/python scripts/gateway_wiring_check.py
```

### 测试

```bash
# 单元测试（不需要 TOKEN）
HSP=$(ls -d "$HERMES_HOME"/installs/*/environments/*/venv/lib/python3.14/site-packages | head -n1)
PYTHONPATH="$HERMES_HOME/hermes-agent:$HSP" .venv/bin/python -m pytest -q
```

> 纯函数单测全绿 ≠ 真实主路径可用。改完插件后至少跑一次
> `scripts/live_probe.py` 或真实发一条消息。

## 消息支持矩阵

### 入站

| msgType | 处理 |
|---|---|
| `0 kText` / `2 kRichText` | 正文文本，HTML 自动去标签 |
| `1/7/14/19/48/63` 图片、`29/104` 表情 | 下载 → 图片缓存 → `MessageType.PHOTO` |
| `101 kWechatImage` | 个人微信图片，走 `/cloud/wxDownload`；原图/中图 502 时回退缩略图 |
| `102 kWechatFile` / `103 kWechatVideo` | 个人微信文件/视频，走 `/cloud/wxDownload` |
| `9/16/21/50/216` 语音 | 下载 → SILK 解码 WAV → STT 转写 |
| `5/17/22/23/51` 视频 | 下载 → 视频缓存 |
| `3/8/15/20/49` 文件 | 下载 → 文档缓存，保留文件名 |
| `6 kLocation` | `[位置] 标题 地址 (纬度,经度)` |
| `13 kLink` | `标题 — 描述 — linkUrl` |
| `4 kForward` / `123 kMixed` | `[聊天记录]` + 子消息（最多 20 条，含发送人）；内嵌媒体一起下载 |
| `26 kHongBaoMsg` | `[红包] 祝福语`（金额只保留原始值） |
| `40 kPVMergeSingleMsg` | `[通话记录] recordWording` |
| `41 kPersonalCard` | `[名片] 姓名 / 昵称 / 企业` |
| `78 kWeAPPMsg` | `[小程序] 标题 — 描述 — 应用名` |
| `141 kWechatSphFeed` | `[视频号] 名称 — 链接` |
| `213 / 529 / 570 / 573` | 接龙 / 朋友圈任务 / 群通知 / 通知卡片 |
| `503 / 2166` | 安全中心、音视频通话 |
| `1001-1006 / 1022 / 1029 / 1043` | 群成员变化 / 群提示 / 群邀请 / 成员管理 |
| `1023/1052/1055/2059/2114/2118/2160/2161/2313/2332/9999` | 通用回退：`display.content` → 常见标题/描述 → `decodedRawData` |
| `38 kAppTextCard` | 应用卡片：只留存、不回复 |
| 其他无具名 body 的 chat 类型 | 用 `nativeName` 占位（如 `[kServiceList]`），避免静默丢消息 |
| `2001` 已读回执 / 撤回 / 空通知 | 丢弃 |

### 出站（实测）

| 类型 | 接口 | 结果 |
|---|---|---|
| 文本 | `sendText` | ✅ |
| 富文本/表情 | `sendHyperText` | ✅ API 接受；个人微信端会退化为纯文本 |
| 链接 | `sendLink` | ✅ API 接受（`13 kLink`） |
| 位置 | `sendLocation` | ✅ API 接受（`6 kLocation`） |
| 个人名片 | `sendPersonalCard` | ✅ API 接受（`41 kPersonalCard`） |
| 文件 | `sendFile` | ✅ |
| 视频 | `sendVideo` | ✅（`23 kVideoFullCDN`） |
| 图片 | `sendImage` | ✅ |
| 语音 | `sendVoice` | ✅ 真实 SILK 语音气泡 |
| 图文（文字+图片） | `sendImageTxt` | ✅ 图片子类型必须用 **14** |
| 大文件 | `sendBigFile` | ✅ `fileId` 必须用上传返回的 **`fileConvertId`**（`*1*…`） |
| 大视频 | `sendBigVideo` | ✅ |
| 聊天记录 | `sendChatRecord` | ❌ HTTP 502（个人微信、企业微信自己、真实 payload 均失败） |

**重要：收件人形态会影响渲染。** 同一次 `/msg/send*` 返回 `isSendSuccess=1`，
只代表 QWPad/企微服务端接受，不代表收件人按原生形态显示。个人微信（外部联系人）
不支持富文本；`kRichText` 的分段/表情会退化为普通文本，原生富文本/卡片形态只在
发给企业微信成员时完整显示。验证渲染效果必须发给企业微信成员并在企微客户端观察。

## 关键设计

### 事件游标与去重

游标按 `profile + TOKEN 指纹` 保存在 `$HERMES_HOME/state/qwpad/`，网关重启后从上次位置继续；
去重集合保留最近 2000 条消息 ID。账号自己发出的消息不会回放到 `/events`，插件也会跳过
`senderId == 自己` 的事件，避免自回环。

### 对话过滤先于鉴权

解析层硬闸：`display.category` 必须是 `chat`，外层事件类型必须是 `15000`（或缺失）。
`system` / `account` / `unknown` 事件不会进入鉴权或对话链路，`ALLOW_ALL_USERS` 也绕不过。

### 引用回复

Hermes 的普通回复不传 `reply_to`，插件按 chat 记住最近一条入站消息作为引用锚点：

- 文本原消息 → `sendHyperText` 两段式正文（首段空串 + 正文）+ `reply`；
- 单段 `sendText` + `reply` 在客户端会显示成“只有引用、正文空白”；
- 真人客户端的四段结构（引号/原文/分隔线）会被 API 发出的 `kRichText` 原样显示，所以不用；
- 非文本原消息暂不引用，退化为普通文本；
- 锚点 10 分钟有效，中间状态消息不消耗锚点；`QWPAD_QUOTE_REPLIES=false` 可关闭。

### 媒体

- 下载统一走 `/cloud/wxDownload` / `/cloud/wxWorkDownload`，缓存到媒体目录；
- `/cloud/files` 签名地址必须带浏览器 User-Agent，否则 403；
- 个人微信原图/中图在 QWPad 侧固定 502，当前只能拿缩略图；
- 入站语音 SILK 解码为 24kHz 单声道 WAV 后交给 STT，不把原始音频塞给聊天模型；
- 出站语音：ffmpeg 转 24kHz PCM → SILK 编码 → `fileType=5` 上传 → `sendVoice`。

### profile 隔离

多账号 = 多 profile。每个 profile 有自己的 `.env`、游标、去重集合和媒体缓存；
`QWPAD_HOME_CHANNEL` 只从当前 profile 读取，不会串用其他 profile 的值。

## 已知限制

- **聊天记录出站不可用**：`sendChatRecord` 稳定 502，已用合成数据、真实事件 payload、
  个人微信与企业微信自己三种目标验证。
- **个人微信不支持富文本**：链接/位置/名片等 API 能发，但客户端可能降级显示。
- **个人微信图片原图/中图 502**：只能拿缩略图，识别质量受限。
- **原生视频/链接/位置/名片/图文/大文件/大视频尚未接入插件**：API 已验证，但适配器目前只暴露
  文本、图片、文件、语音；视频按文件发送。
- **没有“正在输入”状态**：pad 协议无此能力，`send_typing` 是空实现。
- **非聊天事件只留存不通知**：好友申请/变化的身份补全尚未接入
  `/contact/getApplyUserList`、`/contact/getSyncMultiData`。
- **类型目录 578 种，openapi 只有 58 种具名正文**：其余类型靠通用回退或 `nativeName` 占位，
  只保证“不静默丢消息”，不保证正文完整。

## 安全与脱敏

- **TOKEN 只放环境变量**：不要写进 `plugin.yaml`、README、脚本或 `config.yaml`。
- 仓库内**不包含**真实 TOKEN、真实成员 ID、手机号、姓名、聊天内容或媒体文件；
  测试 fixture 使用合成 ID/昵称。
- `.gitignore` 已排除 `.env`、`*.env`、`.testhome/`、`.venv/`、缓存目录。
- 旁路留存日志可能包含联系人元数据，默认 `0600` 且只落本地，不要提交到仓库。
- 提交前建议再扫一遍：

  ```bash
  command grep -rInE 'wxa_[A-Za-z0-9]{16,}|Bearer [A-Za-z0-9._-]{20,}|1[3-9][0-9]{9}' .
  ```

## 故障排查

| 现象 | 排查 |
|---|---|
| API 返回 403 | 请求缺少浏览器 User-Agent（QWPad 对默认 UA 会 403） |
| 新会话提示 “No home channel” | 设置 `QWPAD_HOME_CHANNEL`；0.1.4+ 会按 profile 导出该变量 |
| 引用气泡只有引用、没有正文 | 必须用 `sendHyperText` 两段式（首段空串）+ `reply`，不能用单段 `sendText` |
| 图片只能拿到缩略图 | QWPad 侧原图/中图固定 502，属上游限制 |
| 改代码后行为没变 | 确认是否加载了 profile 级副本；同步后必须重启 host gateway |
| 语音发出去是文件 | 检查 `ffmpeg` 和 SILK 编码器路径（`QWPAD_SILK_ENCODER`） |
| 群消息不回复 | 默认必须 @；检查 `QWPAD_GROUP_REQUIRE_MENTION` 和 `display.category` |
| 聊天记录发送 502 | 当前接口不可用，不是收件人或 payload 问题 |

## 版本

当前版本 **0.1.10**。变更记录见 [CHANGELOG.md](./CHANGELOG.md)。

## License

[MIT](./LICENSE)
