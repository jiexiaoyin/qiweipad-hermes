# CHANGELOG

## Unreleased — 2026-10-22

文档与公开发布准备（无代码行为变更）：

- 重写 `README.md`：项目简介、架构、安装部署、完整配置项、使用说明、入站/出站消息矩阵、
  关键设计、已知限制、安全脱敏与故障排查。
- 公开仓库脱敏：真实成员 ID、昵称、手机号、事件样本 md5 等全部替换为合成值；
  测试 fixture 同步更新，单测保持通过。
- 补充出站实测结论：链接、位置、名片、视频、图文、大文件、大视频均可发送；
  `sendChatRecord` 稳定 502；个人微信端不支持富文本原生渲染。

## 0.1.10 — 2026-10-22

功能：聊天记录内嵌媒体下载 + 出站语音气泡。

- `kForward`（4/123）的 `subMsgList` 里可能有 `subMsgType=101` 等媒体子消息；
  旧实现只渲染 `[图片]`/`[文件]` 占位。新增 `inbound._forward_media_refs()` 收集
  下载凭证，`adapter._dispatch()` 逐条下载后与聊天记录文本一起作为附件交给模型
  （上限 10 条；嵌套聊天记录不递归）。
- 实测事件 292：内嵌图片 `fileBigSize=123456`，`/cloud/wxDownload` 下载成功、
  md5 与子消息一致。
- 出站语音：新增 `client.send_voice`（`POST /msg/sendVoice`）、
  `media.encode_voice_to_silk`（ffmpeg 24kHz mono → silk encoder → `VoiceTime` 向上取整秒）、
  `outbound.send_voice` 与适配器 `send_voice`；SILK 编码不可用时降级为普通文件。
- 实测发送：图片 `msgServerId=1177111`、语音 `msgServerId=1177113`（6 秒 SILK 气泡），
  用户端确认收到。

## 0.1.9 — 2026-10-22

功能演进：对照 QWPad OpenAPI 把入站消息类型补齐，避免「文档里有、插件里静默丢弃」。

- 按 `WW*MessageBody` 具名结构补了聊天正文提取：
  位置（6，含经纬度）、链接（13，优先 `linkUrl`）、聊天记录/图文组合（4/123，渲染
  `subMsgList` 发送人与子消息）、红包（26，祝福语 + 金额原始值）、通话记录（40）、
  名片（41）、小程序（78）、视频号（141）、接龙（213）、安全中心/通话（503/2166）、
  朋友圈任务（529）、群通知（570）、通知卡片（573，解析 `notifyRichContent` JSON 标题）、
  成员变化（1001-1006）、群提示（1022）、群邀请（1029）、成员管理（1043）、好友申请（2357）。
- 通用回退链：`display.content` → 常见 `title/desc/description/url` → `decodedRawData`
  （utf8/json/xml）；1023/1052/1055/2059/2114/2118/2160/2161/2313/2332/9999 等
  `WWFallback` 类型都能出正文。
- 媒体类型按文档补全：图片 1/7/14/19/29/48/63/104、语音 9/16/21/50/216、
  视频 5/17/22/23/51、文件 3/8/15/20/49；个人微信 102/103 走 `/cloud/wxDownload`。
- `msg_name` 优先用 `display.messageType.nativeName`（类型目录 578 项，本地常量表只列常用）。
- 未具名 body 的 chat 类型：`parseStatus` 为 parsed/parsed_media/partial/unsupported 时
  用 `nativeName` 占位（如 `[kServiceList]`）；`no_body/empty_body` 仍丢弃。
- 新增 12 个解析单测（82 单测全绿）；45 条真实历史事件回放保持 18 条入对话，无新增噪音。

## 0.1.8 — 2026-10-22

修复：入站个人微信图片（`msgType=101 kWechatImage`）被静默丢弃，用户发图后机器人不响应。

- 根因：`MEDIA_MSG_TYPES` 没有 101，`parse_event` 返回 `None`，事件被跳过且没有日志。
- 新增 `MSG_WECHAT_IMAGE=101` / `kWechatImage`，101 映射为图片媒体。
- 101 没有 `fileId`，改用 `fileBigHttpUrl` / `fileMiddleHttpUrl` / `fileThumbHttpUrl`
  + `fileAuthKey` / `fileAesKey` 走 `/cloud/wxDownload`；按 big → middle → thumb 依次回退。
- 新增 `QWPadClient.download_wx_file`；`/cloud/files` 签名地址必须带浏览器 UA（否则 403）。
- ⚠️ 实测限制：QWPad 侧原图/中图转存固定 502（异步任务 500100），目前只能拿到缩略图
  （示例 180×128、3067 字节）；小图识别质量受限。
- 新增 `tests/test_media_download.py` 与 101 解析单测；真实事件 221 主路径验证通过。

## 0.1.7 — 2026-10-22

修复 0.1.6 的引用正文方案：四段结构虽然能让正文显示，但 `"`、原文和 `------`
会作为普通文字显示在消息里。

- 根因：真人客户端的四段结构只在 `msgType=0 kText` 下被解析；QWPad API 发出的
  所有文本消息都是 `msgType=2 kRichText`，引用块固定为 `f1002.f4=1`（只引用模式），
  客户端会把四段正文原样显示。
- API 侧可行解：`/msg/sendHyperText` 正文用两段——首段空串 + 第二段回复内容，同时带
  `reply`。实测客户端正常显示引用气泡和正文，且没有引号/分隔线。
- 首片走 `sendHyperText`，超长后续分片仍走普通文本；非文本原消息暂不引用。
- 新增/更新 `send_quoted_text` 单测与适配器引用单测。

## 0.1.6 — 2026-10-22（已被 0.1.7 取代）

修复：引用回复在企业微信客户端里「只有引用、正文空白」。

- 根因：`/msg/sendText` + `reply` 生成的是**单段** kRichText；真人客户端引用文本时
  实际发的是四段：`"` / `原文` / `"\n------\n` / `新内容`。客户端只认四段结构，
  单段正文会渲染成空白（正文在服务端 `syncMsg` 里其实是完整的）。
- 改走 `/msg/sendHyperText`：首片按四段发送并带 `reply`，后续分片仍走普通文本。
- 非文本原消息（图片/文件等）没有可嵌入四段正文的原文，退化为普通文本，避免空白气泡。
- 引用原文先去 HTML 标签（kRichText 的 `content` 可能带 `<div>` 片段），避免引用气泡显示标签。
- 实测：适配器发出的消息在 `syncMsg` 中为四段；用户端确认四段结构的正文可见。
- 新增 `QWPadClient.send_hyper_text`、`outbound.send_quoted_text` 及对应单测。

## 0.1.5 — 2026-10-22

功能演进：**文本回复自动引用本轮用户消息**（企业微信「引用回复」气泡）。

- Hermes 的普通回复不传 `reply_to`，插件按 chat 记住最近一条入站消息，在发送时自动带上
  QWPad `/msg/sendText` 的 `reply` 对象（`msgUniqueIdentifier` / `showName` / `timeStamp` /
  `type` / `userId` / 原 `msgData`）。
- 中间状态消息（`_interim_send`）不消耗锚点，最终回复仍会引用；文本分片只引用第一片。
- 10 分钟自动引用窗口，避免 cron/通知误引用旧消息；`QWPAD_QUOTE_REPLIES=false` 可关闭。
- 默认值从 false 改为 true。
- 顺带修复私聊昵称：`/contact/batchGetUserinfo` 的姓名在 `nickname` / `realName` /
  `fullUserInfo.name`，之前只读 `name` 导致会话名和引用气泡显示数字 ID。
- 新增 `tests/test_adapter_quote.py`。

## 0.1.4 — 2026-10-22

修复：每个新会话都会弹「📬 No home channel is set for Qwpad」。

- 冒烟：`QWPAD_HOME_CHANNEL` 只存在于 profile `.env`，网关进程环境里没有；而提示的兜底检查
  会读 `os.getenv("QWPAD_HOME_CHANNEL")`，于是即使 profile 配置正确也会误报。
- 插件注册 / `env_enablement_fn` 时把自己的 home channel 导出到进程环境（已存在则不覆盖）。
  值按插件路径反推所属 profile，只读本 profile 的 `.env` / secret scope，不会串用别的 profile。
- 新增 `tests/test_registration_home_channel.py`（profile 副本、home 副本、已存在值不覆盖、缺省不导出）。

## 0.1.3 — 2026-10-22

功能演进：**非聊天事件旁路留存**（不进对话、不回复）。

- `/events` 不再在服务端按 `type=15000` 过滤；单游标拉全量，新增 `retained.classify_event`
  分流 `chat / retain / drop`，游标对所有事件照常推进。
- 默认留存 `friend_apply`（2357/2132）、`friend_change`（2131）、`approval`（145）、
  `cc`（561）、`relationship`（1011）、`app_card`（38）、`account_meta`（11004）；
  已读回执 `2001`、未知空通知 `2180` 仍丢弃。
- 旁路日志 `$HERMES_HOME/state/qwpad/retained-<token指纹>.jsonl`：0600、10MB×5 轮转、
  默认 30 天清理；结构化事件保留 `data`，145/561 解出内嵌卡片 JSON，`account_meta` 只留元数据。
- `kAppTextCard`(38) 即使 `category=chat` 也只留存、不回复，避免应用通知触发对话。
- 新增 `tests/test_retained.py`；用 29 条真实历史事件验证分流（7 chat / 13 retain / 9 drop）。

## 0.1.2 — 2026-10-22

功能演进：**只处理群聊和私聊**，其它会话一律不回复（含 `ALLOW_ALL_USERS=1` 放行模式）。

- 解析层硬闸：`display.category` 必须是 `chat`；`system`（系统通知）、`account`（设备/账号状态）、
  `unknown`（待解析通知）直接丢弃，不进入鉴权/对话链路。
- 外层事件 `type` 必须是 `15000`（消息）；`15500`（系统消息）等一律丢弃。
- 依据：QWPad OpenAPI `RealtimeDisplay.category` 枚举与 `WWNotifySdk.type` 定义。

## 0.1.1 — 2026-10-22

修复：入站消息全部被 Hermes 原生准入拒绝（`invalid_params (metadata)`）。

- 根因：`_dispatch` 给 `MessageEvent` 塞了 `metadata={"qwpad_msg_type", "qwpad_event_id"}`，
  而 Hermes 的 metadata 契约是封闭的（`gateway/session_envelope.py:_native_metadata` 只放行
  WhatsApp 的专用键）。其它平台带非空 metadata ⇒ 消息不落库、不回答，只回一段 120 字符拒绝提示。
- 修复：不再设置 `metadata`；原始事件仍在 `raw_message` 里。
- 回归测试：`tests/test_adapter_contract.py` 用核心的 `_native_metadata` 验证事件形状。

## 0.1.0 — 2026-10-22

首个可用版本：QWPad（企业微信 pad 协议）作为 Hermes 平台适配器。

- 平台注册（`ctx.register_platform`）+ `env_enablement_fn`：`QWPAD_TOKEN` 一填，平台自动进
  `config.platforms`，`hermes gateway status` 可见。
- 收消息：`/events` long-poll（unified），游标按 profile + TOKEN 指纹落盘，去重集合 2000 条，
  账号 5 分钟一次在线自检。
- 发消息：文本（markdown 清洗 + 2000 字分片）、图片、文件；cron 的进程外发送
  （`standalone_sender_fn`）。
- 入站：文本 / 富文本 / 图片 / 语音（SILK→WAV）/ 视频 / 文件 / 链接 / 位置 / 卡片；
  群消息必须 @（可关），@ 前缀自动剥除，引用原文进上下文。
- 媒体：`/cloud/wxWorkDownload` + 媒体缓存；SILK 解码器缺失时降级为文件。
- 测试：24 个单测 + 3 个 opt-in LIVE 冒烟 + 两个真实环境探针脚本。
