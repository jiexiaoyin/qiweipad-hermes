# 本账号实测过的 QWPad API 事实（2026-10-22）

> 只记「文档没写清、实测才知道」的部分，避免下次重踩。

## 认证 / 传输

- `Authorization: Bearer wxa_...`；**不是** `X-Admin-Key`（那是 `wxm_` 管理 Key）。
- 直接 `curl` 不带 User-Agent 拉 `openapi.json` 会 403（拿文档时要带 UA）。
- 业务返回统一 `{code,msg,data}`；认证失败码 `401400`（不可重试），参数校验 `422100`。

## 收消息 `GET /events`

- `format=unified` 时：`data.items[] = {id, type, display{...}, message{msgType,msgData,structured{...}}}`，
  `data.nextId` 是下一页游标，`data.hasMore`。
- `type=15000` 只过滤外层事件类型；**聊天消息内部还有 `msgType`**（2001 = kReportRead 已读回执，属于噪音）。
- `waitSeconds` 上限 30；有消息时立刻返回，没有则挂到超时。
- **账号自己发出的消息不会回放到 `/events`**（实测 sendText 后 events 里没有对应条目）。
- 首次 `afterId=0` 只返回该 TOKEN 激活后的事件，不含历史聊天记录（历史要用 `/msg/syncMsg`）。
- `senderId` / `fromRoomId` / `receiverId` 都是十进制字符串（可到 17 位以上），必须按 string 处理；
  `fromRoomId == "0"` 表示私聊。

## 非聊天事件（2026-10-22 全量 29 条样本）

- `display.category` 枚举：`chat` / `system` / `account` / `unknown`。样本分布：
  `chat=8`、`system=1`、`account=0`、`unknown=20`。
- **`type=15000` 会漏掉全部 15500 系统消息**（好友申请、好友变化、审批抄送等）。
  要保留这些事件，必须不带 `type` 拉全量，再由插件本地分类。
- 实测非聊天类型：
  - `2357 E_CONTENT_FRIEND_APPLY_OTHER_CORP`（system/15500）：`structured`，带
    `contactId/contactNickname/contactType/userId/applyTime`，`contactApply` 子字段 partial。
  - `2132 kFriendApply`、`2131 kFriendChanged`（unknown/15500）：`no_business_fields`，
    只有 `extras:null`，只能当触发信号；补身份用 `POST /contact/getApplyUserList`、
    `POST /contact/getSyncMultiData`（后者 `bizType=1/2` 同步客户资料）。
  - `145 E_CONTENT_FUNVIEW_UNION`（unknown/15000）：`raw_preserved`，`base64RawData`
    解码后是「长度前缀 + 内嵌卡片 JSON」，可 `json.JSONDecoder().raw_decode` 抠出；
    标题在 `main_title.title`，状态在 `main_title.sub_title`。
  - `561 E_CONTENT_FUN_VIEW_DATA_TIPS`（unknown/15500）：同上，抄送/发票类卡片。
  - `1011 kTextControl`（unknown/15000）：`decodedRawData.text` 是“以上是打招呼内容”
    “你已添加了…”，适合做关系时间线，不适合进对话。
  - `2001 kReportRead`（unknown，15000/15500 都有）：`no_business_fields`，已读回执噪音。
  - `2180`：类型目录里没有（`known=false`），空 body，丢弃。
  - `11004`：无 `msgType`，`message.rawBodyBase64` 是不透明 protobuf
    （实测 field1=1023，field2=本账号 userId），只留事件元数据。
  - `38 kAppTextCard` 可能以 `category=chat` 出现（服务号通知），不能当人类私聊回复。

## 发消息

- `POST /msg/sendText {toId, content}`：`toId` 可以是自己的 `userId`（能发给自己）。
- 返回 `data.msgServerId` / `isSendSuccess=1` / `msgUniqueIdentifier`。
- 发给自己或不存在/不可发送的对象时是 **HTTP 502**（业务层直接失败，不是 4xx）：
  `sendText → 智能小秘书 (33776900000000001)` 实测 502。
- `/msg/sendImage` 需要先 `POST /cloud/cdnBigUpload`（multipart，`fileType`: 1 图片 / 4 视频 / 5 文件/语音），
  拿 `fileId,fileAesKey,fileSize,fileMd5` 再发；`/msg/sendFile` 额外要 `fileMd5`。
- 文本按**字符**限流约 2000，超长客户端应自行分片。

### 收件人形态：个人微信 vs 企业微信成员（2026-10-22 用户端确认）

- 同一次 `/msg/send*` 返回 `isSendSuccess=1`，只代表 QWPad/企微服务端接受，
  **不代表收件人按原生形态渲染**。
- 用户端实测：**发到个人微信（外部联系人）不支持富文本**；kRichText 的分段/表情在
  个人微信里会退化为普通文本。原生富文本/卡片形态只在发给**企业微信成员**时完整显示。
- 本日矩阵测试的 `toId=7881300000000001` 是个人微信，所以 1-7 的“成功”只证明消息能发出去、
  `syncMsg` 里类型正确（13 kLink / 6 kLocation / 41 kPersonalCard / 23 kVideoFullCDN 等），
  **不能证明个人微信端按卡片渲染**。要验证原生渲染，必须发给企业微信成员并在企微客户端观察。
- `/msg/sendChatRecord` 对个人微信和企业微信自己都返回 HTTP 502（把真实事件 276 的
  `subMsgList` 原样重放也 502），所以不是收件人形态问题，当前按“接口不可用”处理。

### 出站语音（2026-10-22 实测）

- `POST /msg/sendVoice`：先 `POST /cloud/cdnBigUpload`（multipart，`fileType=5`）上传
  **SILK** 文件，再把同次上传的 `fileId` / `fileAesKey` / `fileSize` 原样复制到发送 JSON，
  外加 `VoiceTime`（秒，按实际时长向上取整，不能填 0）。
- 编码链：`ffmpeg -ar 24000 -ac 1 -f s16le` → `silk encoder in.pcm out.silk -tencent -quiet`
  （本机 `~/silk_decoder/silk/encoder`）。SILK 头为 `0x02#!SILK_V3`。
- 实测：5.76 秒 TTS WAV → 17,052 字节 SILK，`VoiceTime=6`；发送成功
  `msgServerId=1177113`，用户端收到可播放语音气泡。
- 插件已实现：`media.encode_voice_to_silk()` + `outbound.send_voice()` +
  适配器 `send_voice()`；编码器/ffmpeg 不可用时降级为普通文件。

### 引用回复（2026-10-22 实测）

- `/msg/sendText` 支持 `reply` 对象，字段：
  `msgUniqueIdentifier`（**非空才引用**）、`showName`、`timeStamp`（秒）、`type`（原 msgType）、
  `userId`（原发送人）、`msgData`（**原消息正文原样复制**，图片/文件引用也靠它）。
- 实测发送 `reply={msgUniqueIdentifier:"1382170000000000000", showName:"测试用户",
  timeStamp:1700000000, type:2, userId:"7881300000000001", msgData:{content:"…",…}}`
  返回 `isSendSuccess=1`，`msgType` 会变成 2（kRichText）。
- **人工在客户端引用的消息**进 `/events` 时可能被压平成 `kText`：
  `"原文"\n------\n新内容`，`msgData.reply=null`、`referId="0"`（见事件 200，人工用本账号引用）。
  所以入站引用不能只信 `reply` 字段，必要时需解析这个文本格式。
- 已实现：`inbound.split_flattened_quote()` 在 `parse_event` 里兜底还原上面的压平形态
  （结构化 `reply` 优先；引用头必须成对引号包裹、分隔线 `-{3,}`、且有新正文才拆）。
- 出站自动引用：`QWPAD_QUOTE_REPLIES=true` 打开后，适配器为每条入站记住 reply 锚点，
  回复时带 `reply` 对象；窗口 `QUOTE_WINDOW_S`。
- **只带 `reply` 的 `/msg/sendText` 会「只有引用、正文空白」**（2026-10-22 用户端实测）：
  服务端 `syncMsg` 里正文完整，但消息类型是单段 `kRichText`，企业微信客户端不渲染正文。
- **真人客户端引用的原始结构**（`syncMsg` 1176884）：外层 `msgType=0 kText`，正文是四段：
  `"` / `原文` / `"\n------\n` / `新内容`；引用块 `field 1002` 里带 `signedMsg`、原消息
  `msgUniqueIdentifier`（`f1.f16` 与 `f2`）、预览文本（`f6`）等完整字段。
- **四段正文不能用在 API 发出的消息上**：`/msg/sendHyperText` 传四段列表
  `[{'\"'},{原文},{'\"\\n------\\n'},{新内容}]` + `reply` 虽然能让正文显示，但客户端是
  kRichText，会把引号、原文、`------` 全部原样显示在消息里（用户端实测）。API 发出的
  所有文本消息 `msgType` 都是 2，无法产生真人客户端的 `kText`。
- **API 引用的引用块固定是「只引用」模式**：`f1002.f4=1`，无 `f6` 预览，`f1` 只有
  `[1,2,5,11,101]`；真人客户端是 `f4=3` + `f6` + 完整 `f1`（含 `f16`/`f17`）。
  只带 `reply`、正文单段的 `sendText`/`sendHyperText` 都会「只有引用、正文空白」。
- **可行解（2026-10-22 用户端实测）**：`/msg/sendHyperText` 正文传两段
  `[{"subtype":0,"text":""},{"subtype":0,"text":新内容}]` + `reply`。客户端正常显示
  引用气泡和正文，且没有引号/分隔线。首段为空是触发正文渲染的关键，不能省略。
- 非文本原消息的引用渲染尚未验证；插件暂时退化为普通文本。

## 群 / 联系人

- `POST /room/getRoomList {limit}` → `roomList[]`（含 memberList）。
- `POST /room/getRoomDetail {roomId}`：**把非群 ID 传进去会 502**，所以 `get_chat_info` 必须能降级
  到 `POST /contact/batchGetUserinfo {userIdList}`。
- `POST /contact/batchGetUserinfo` 对成员 ID 返回 `contactList[]`（`name` / `acctid` / `fullUserInfo`）。

## msgType（`POST /msg/getMessageTypeList` 实测）

`0 kText` / `1 kImage` / `2 kRichText` / `3 kFile` / `4 kForward` / `5 kVideo` / `6 kLocation` /
`9 kVoice` / `13 kLink` / `14 kContentImageCDN` / `15 kContentFileCDN` / `16 kVoiceCDN` /
`17 kVideoCDN` / `23 kVideoFullCDN` / `38 kAppTextCard` / `2001 kReportRead`。

真实样本（示例企业账号 100 条历史）：57×`kRichText`、25×`2001`、7×561、4×35、3×573、1×132、
1×105、1×38、1×10。`kRichText` 正文在 `msgData.content`；`kAppTextCard` 正文是 HTML 片段
（`content` 里带 `<div class=...>`），要剥标签。

## 个人微信图片 `msgType=101 kWechatImage`（2026-10-22 实测）

- 事件样本 221（私聊）：`display.category=chat`、`display.content.kind=image`，
  `msgData` 没有 `fileId`，只有：
  `fileAesKey`（16 字节 hex）、`fileAuthKey`（131 字符）、`fileBigHttpUrl` /
  `fileMiddleHttpUrl` / `fileThumbHttpUrl`（都是 `imunion.weixin.qq.com/cgi-bin/mmae-bin/tpdownloadmedia?param=…`）、
  对应 `fileBigSize` / `fileMiddleSize` / `fileThumbSize`、`fileMd5`、`imageHasHd`。
  `fileInfo` 里是同一组值的结构化副本（`img.imgUri` = 中图，`openimCdnLdUrl` = 缩略图）。
- 下载只能走 `POST /cloud/wxDownload`（`fileUrl` + `fileAuthkey` + `fileAeskey` + `fileSize` + `fileType`），
  返回 `data.cloudUrl`（`https://api.qwpad.com/cloud/files?key=…&expires=…&signature=…`）。
- **`/cloud/files` 必须带浏览器 User-Agent**：httpx 默认 UA 返回 403，`Mozilla/5.0 …` 返回 200。
- **原图/中图固定失败**：`fileType=1`（原图）与 `2`（中图）无论尺寸参数如何组合都返回 HTTP 502；
  `/cloud/wxDownloadAsync` 接受任务后回调「异步任务失败 错误码：500100」；
  `/cloud/cdnBigUploadByUrl`（通用网址转存）同样 502。缩略图 `fileType=3` 稳定成功。
- 直连 `imunion.weixin.qq.com`（带任何 UA/Referer/Range/鉴权头）从本机返回 400、0 字节，
  所以无法绕开 QWPad 自行下载。
- 结论：适配器按 big(1) → middle(2) → thumb(3) 回退；当前实际只能拿到缩略图（样本 180×128、3067 字节）。
  原图需要 QWPad 侧修复，或换其他可下载的媒体形态（例如让用户以文件发送）。

## 聊天记录 `kForward` 内嵌媒体（2026-10-22 实测）

- `msgData.subMsgList[]` 里的媒体子消息用与顶层 **同一套** `subMsgType` 枚举；事件 292
  （测试用户和示例机构联系人B的聊天记录）中图片子消息 `subMsgType=101`，`subMsgData` 直接带
  `fileAesKey` / `fileAuthKey` / `fileBigHttpUrl` / `fileBigSize` / `fileMd5` /
  `fileThumbHttpUrl` 等，与独立 `kWechatImage` 的字段完全一致。
- 下载同样走 `/cloud/wxDownload`（`fileUrl` + `fileAuthkey` + `fileAeskey` + `fileSize` +
  `fileType`）；本次 big（`fileType=1`）成功，123456 字节，md5 与子消息一致。
- 适配器已实现：`inbound._forward_media_refs()` 从 `subMsgList` 收集可下载媒体，
  `adapter._dispatch()` 逐条下载后与聊天记录文本一起作为附件交给 Hermes；模型看到的是
  文本里的 `[图片]` 占位行 + 真正的图片，不再只有占位。
- 注意：**第一条**转发记录（事件 276）确实只有两条文本子消息，没有图片；图片是随后单独
  发的 `kWechatImage`（事件 282），以及第二条转发记录（事件 292）里的内嵌子消息。

## 消息类型覆盖（2026-10-22 对照 openapi）

- `/msg/getMessageTypeList` 返回 **578** 种 msgType；openapi 只有 **58** 种在
  `RealtimeUnifiedMessage.allOf` 里给了具名正文（`WW*MessageBody`）。
- 每个事件都带 `display.messageType = {msgType, nativeName, bodyProjection, known}` 和
  `display.parseStatus`（parsed / parsed_media / partial / no_body / empty_body / unsupported）。
  本地常量表只维护常用类型，`msg_name` 优先取 `nativeName`。
- `bodyProjection`：`structured`（有具名 body）/ `raw_preserved`（只有
  `base64RawData` + `decodedRawData`）/ `no_business_fields`（空通知）。
- `syncMsg responseFormat=structured` 时，raw/unsupported 类型的 `msgData` 为 null，
  `unparsedFields` 会写 `message_type_has_no_verified_business_body_decoder`；
  `/events` 里则退到 `display.content`（统一视图：kind/title/text/description/url/media）
  和 `message.msgData.decodedRawData`（format: empty/protobuf/json/xml/utf8/bytes）。
- 解析策略：具名类型逐个提取 → `display.content` 通用回退 → `decodedRawData`；
  仍无正文时，`parseStatus` 非 `no_body/empty_body` 用 `[nativeName]` 占位，避免静默丢消息。
- 通知类（1022/1043/570/573/1001-1006 等）虽然能解析，但是否进对话仍由
  `display.category` 决定；服务端标成 `system/unknown` 的只走旁路留存。

## 语音转写（2026-10-22 实测）

- 入站语音（16 `kVoiceCDN`）插件已能下载 SILK → 解码成 WAV（24 kHz 单声道 16-bit），
  转写由 Hermes STT 完成，**不会把原始音频交给聊天模型**（即使模型多模态）。
- 百炼 `qwen-audio-3.0-asr-flash` 走**原生 DashScope 多模态端点**：
  `POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`，
  body `{model, input:{messages:[{role:"user",content:[{type:"input_audio",input_audio:{data:"data:audio/wav;base64,..."}}]}]}, parameters:{format:"wav"}}`；
  结果为 `output.text`。**它不是 OpenAI 兼容的 chat/completions 模型**——把
  `input_audio` 发到 `/compatible-mode/v1/chat/completions` 会报 `format is empty`。
- 本机已接到 Hermes command STT：`$HERMES_HOME/bin/bailian_asr.py`，
  四个 profile 的 `stt.provider: bailian-asr`，key 走 `env_passthrough`
  （`BAILIAN_EMBEDDING_API_KEY` 等，值不进 config）。
- 聊天模型 `qwen3.8-flash` 实测支持图片输入（vision），**不支持音频**
  （`input_audio` 报 `incorrect modal audio`）；所以语音必须先经 STT 转文字。

## 待验证

- 图片 / 语音 / 文件消息的 `msgData` 真实字段名（历史里没有媒体消息）——
  适配器用多键兜底提取（`fileId|fileid|cdnFileId`、`fileAeskey|fileAesKey|fileKey`、
  `fileSize|size`、`fileMd5|md5`、`filename|fileName|title`），需要在真实媒体事件上确认。
- 群消息 @ 的 `atList` 结构与正文里 @ 前缀的精确形态（当前两种都认）。
