"""入站解析单测 —— 用 live 抓到的 unified 事件形状当夹具。"""

from __future__ import annotations

import copy

from qwpad.inbound import parse_event, reply_context

SELF_ID = "1688850000000001"
SELF_NICK = "测试用户"


def _event(msg_type: int, data: dict, *, room_id="0", sender="1688851111111111",
           sender_name="张三", msg_id="MSG-1", display_text="", is_revoked=False) -> dict:
    return {
        "id": 1001,
        "createdAt": "2026-10-22 09:00:00",
        "type": 15000,
        "display": {
            "category": "chat",
            "text": display_text,
            "senderId": sender,
            "senderName": sender_name,
            "roomId": room_id,
            "messageId": msg_id,
            "messageType": {"msgType": msg_type, "known": True, "nativeName": "x"},
            "content": None,
        },
        "message": {
            "msgType": msg_type,
            "msgData": data,
            "structured": {
                "senderId": sender,
                "senderName": sender_name,
                "fromRoomId": room_id,
                "receiverId": SELF_ID,
                "msgType": msg_type,
                "msgData": data,
                "msgUniqueIdentifier": msg_id,
                "isRevoked": is_revoked,
                "timestamp": 1792627702,
            },
        },
    }


def test_dm_text():
    inbox = parse_event(_event(0, {"content": "你好"}), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.text == "你好"
    assert inbox.chat_type == "dm"
    assert inbox.chat_id == "1688851111111111"
    assert inbox.user_name == "张三"
    assert inbox.is_self is False
    assert inbox.mentions_me is False


def test_group_mention_detected_and_stripped():
    data = {
        "content": "@测试用户 这个月销量多少",
        "atList": [{"userId": SELF_ID, "nickname": SELF_NICK}],
    }
    inbox = parse_event(_event(0, data, room_id="10888888888888888"),
                        self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.chat_type == "group"
    assert inbox.chat_id == "10888888888888888"
    assert inbox.mentions_me is True
    assert inbox.text == "这个月销量多少"


def test_group_without_mention():
    inbox = parse_event(_event(0, {"content": "大家早"}, room_id="10888888888888888"),
                        self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None and inbox.mentions_me is False


def test_image_media_ref():
    data = {
        "fileId": "FILE-ABC",
        "fileAeskey": "AES-KEY",
        "fileSize": 12345,
        "filename": "photo.jpg",
    }
    inbox = parse_event(_event(1, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.media is not None
    assert inbox.media.kind == "image"
    assert inbox.media.file_id == "FILE-ABC"
    assert inbox.media.aes_key == "AES-KEY"
    assert inbox.text == "[图片]"


def test_wechat_image_media_ref():
    """msgType 101：没有 fileId，只有 imunion 直链 + fileAuthKey。"""
    data = {
        "fileAesKey": "AES-101",
        "fileAuthKey": "AUTH-101",
        "fileBigHttpUrl": "https://imunion.weixin.qq.com/big",
        "fileBigSize": 1213216,
        "fileMiddleHttpUrl": "https://imunion.weixin.qq.com/mid",
        "fileMiddleSize": 39783,
        "fileThumbHttpUrl": "https://imunion.weixin.qq.com/thumb",
        "fileThumbSize": 3067,
        "fileMd5": "MD5-101",
    }
    inbox = parse_event(_event(101, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None and inbox.media is not None
    assert inbox.media.kind == "image"
    assert inbox.media.file_id == ""
    assert inbox.media.auth_key == "AUTH-101"
    assert [c["fileType"] for c in inbox.media.wx_candidates] == [1, 2, 3]
    assert inbox.media.wx_candidates[0]["fileUrl"].endswith("/big")
    assert inbox.media.downloadable is True
    assert inbox.text == "[图片]"


def test_file_media_ref():
    data = {"fileId": "F-1", "fileAesKey": "K", "fileSize": 99, "fileName": "报表.xlsx"}
    inbox = parse_event(_event(3, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None and inbox.media is not None
    assert inbox.media.kind == "file"
    assert inbox.media.filename == "报表.xlsx"


def test_self_message_is_flagged():
    inbox = parse_event(_event(0, {"content": "我发的"}, sender=SELF_ID, sender_name=SELF_NICK),
                        self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None and inbox.is_self is True


def test_read_receipt_noise_dropped():
    assert parse_event(_event(2001, {"content": ""}), self_id=SELF_ID, self_nick=SELF_NICK) is None


def test_revoked_dropped():
    assert parse_event(_event(0, {"content": "撤回"}, is_revoked=True),
                       self_id=SELF_ID, self_nick=SELF_NICK) is None


def test_reply_context():
    data = {"content": "收到", "reply": {"msgData": {"content": "原始问题"}, "showName": "李四"}}
    inbox = parse_event(_event(0, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert reply_context(inbox) == "原始问题"


def test_display_text_fallback():
    item = _event(2, {})
    item["display"]["text"] = "富文本正文"
    inbox = parse_event(item, self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None and inbox.text == "富文本正文"


def test_unknown_type_without_body_dropped():
    assert parse_event(_event(15000, {}), self_id=SELF_ID, self_nick=SELF_NICK) is None


def test_app_text_card_html_stripped():
    data = {
        "title": "续费提醒",
        "content": '<div class="gray">2026年07月25日</div> <div class="normal">正在使用的【高级版】7天后到期</div>',
        "url": "https://example.com/renew",
    }
    inbox = parse_event(_event(38, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert "<div" not in inbox.text
    assert "续费提醒" in inbox.text
    assert "7天后到期" in inbox.text
    assert "https://example.com/renew" in inbox.text


def test_html_in_plain_text_is_stripped():
    inbox = parse_event(_event(0, {"content": "<b>你好</b><br>世界"}),
                        self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.text == "你好\n世界"


def test_does_not_mutate_input():
    item = _event(0, {"content": "hi"})
    snapshot = copy.deepcopy(item)
    parse_event(item, self_id=SELF_ID, self_nick=SELF_NICK)
    assert item == snapshot


def test_only_chat_category_is_parsed():
    """群聊/私聊之外的一切会话（系统通知、账号状态、待解析）都不得进入 Hermes。"""
    for category in ("system", "account", "unknown", "", None):
        item = _event(0, {"content": "通知"})
        if category is None:
            item["display"].pop("category", None)
        else:
            item["display"]["category"] = category
        assert parse_event(item, self_id=SELF_ID, self_nick=SELF_NICK) is None, category


def test_chat_category_group_and_dm_still_parsed():
    dm = parse_event(_event(0, {"content": "私聊"}), self_id=SELF_ID, self_nick=SELF_NICK)
    group = parse_event(_event(0, {"content": "@测试用户 群聊"}, room_id="ROOM-1"),
                        self_id=SELF_ID, self_nick=SELF_NICK)
    assert dm is not None and dm.chat_type == "dm"
    assert group is not None and group.chat_type == "group"


def test_non_chat_event_type_dropped():
    """外层 type != 15000（如 15500 系统消息）同样丢弃。"""
    item = _event(0, {"content": "系统消息"})
    item["type"] = 15500
    assert parse_event(item, self_id=SELF_ID, self_nick=SELF_NICK) is None


# ── 人工「引用」被压平成 "原文"\n------\n新内容（2026-10-22 实测形态） ──────────
def test_flattened_quote_is_split_into_reply_and_body():
    content = '"没啥事，我就是做测试"\n------\n好的'
    inbox = parse_event(_event(0, {"content": content, "reply": None}),
                        self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.text == "好的"
    assert reply_context(inbox) == "没啥事，我就是做测试"


def test_structured_reply_wins_over_flattened_text():
    """两种形态同时出现时，结构化 reply 优先，正文不再拆。"""
    content = '"旧话"\n------\n新话'
    data = {"content": content, "reply": {"msgData": {"content": "结构化原文"}}}
    inbox = parse_event(_event(0, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert reply_context(inbox) == "结构化原文"
    assert inbox.text == content


def test_separator_without_quoted_head_is_not_split():
    content = "第一段\n------\n第二段"
    inbox = parse_event(_event(0, {"content": content}), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.text == content
    assert inbox.reply is None


def test_quote_only_message_keeps_original_text():
    """只有引用没有新正文时保持原样，不能把消息拆成空文本丢掉。"""
    content = '"原文"\n------\n'
    inbox = parse_event(_event(0, {"content": content}), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.text == content.strip()
    assert inbox.reply is None


# ── 按 openapi WW*MessageBody 补齐的聊天类型 ──────────────────────────────────
def test_link_uses_link_url_field():
    data = {"title": "价格表", "desc": "2026 秋季", "linkUrl": "https://example.com/p",
            "url": "https://stale.example.com/old"}
    inbox = parse_event(_event(13, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert "价格表" in inbox.text and "2026 秋季" in inbox.text
    assert "https://example.com/p" in inbox.text
    assert "stale" not in inbox.text


def test_location_has_address_and_coordinates():
    data = {"title": "示例门店", "address": "示例路 1 号", "latitude": 33.01, "longitude": 118.54}
    inbox = parse_event(_event(6, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.text.startswith("[位置]")
    assert "示例门店" in inbox.text and "示例路" in inbox.text
    assert "33.01" in inbox.text and "118.54" in inbox.text


def test_combined_message_renders_sub_messages():
    data = {
        "subMsgDesc": "今天的安排",
        "subMsgList": [
            {"subMsgType": 2, "subMsgSenderName": "张三",
             "subMsgData": {"content": "下午开会"}},
            {"subMsgType": 14, "subMsgSenderName": "李四",
             "subMsgData": {"fileId": "F-1", "filename": "议程.png"}},
            {"subMsgType": 15, "subMsgSenderName": "李四",
             "subMsgData": {"fileId": "F-2", "filename": "议程.docx"}},
        ],
    }
    inbox = parse_event(_event(4, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.text.startswith("[聊天记录]")
    assert "今天的安排" in inbox.text
    assert "张三: 下午开会" in inbox.text
    assert "[图片]" in inbox.text
    assert "[文件] 议程.docx" in inbox.text


def test_combined_message_collects_embedded_media_refs():
    """聊天记录里的图片子消息要带出下载凭证，不能只剩一个 [图片] 占位。"""
    data = {
        "subMsgDesc": "测试用户和某人的聊天记录",
        "subMsgList": [
            {"subMsgType": 2, "subMsgSenderName": "测试用户",
             "subMsgData": {"content": "收到"}},
            {"subMsgType": 101, "subMsgSenderName": "测试用户",
             "subMsgData": {
                 "fileAesKey": "a" * 32,
                 "fileAuthKey": "v1_auth",
                 "fileBigHttpUrl": "https://imunion.weixin.qq.com/big",
                 "fileBigSize": 123456,
                 "fileMiddleHttpUrl": "https://imunion.weixin.qq.com/mid",
                 "fileMiddleSize": 100,
                 "fileThumbHttpUrl": "https://imunion.weixin.qq.com/thumb",
                 "fileThumbSize": 10,
                 "fileMd5": "0123456789abcdef0123456789abcdef",
                 "filename": "shot.jpg",
             }},
            {"subMsgType": 2, "subMsgSenderName": "测试用户",
             "subMsgData": {"content": "对账登录不了"}},
        ],
    }
    inbox = parse_event(_event(4, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert "测试用户: [图片]" in inbox.text
    assert len(inbox.media_refs) == 1
    ref = inbox.media_refs[0]
    assert ref.kind == "image" and ref.downloadable
    assert ref.auth_key == "v1_auth"
    assert ref.size == 123456 and ref.md5 == "0123456789abcdef0123456789abcdef"
    assert [c["fileType"] for c in ref.wx_candidates] == [1, 2, 3]


def test_red_envelope_wish_kept_amount_not_converted():
    inbox = parse_event(_event(26, {"wishingContent": "恭喜发财", "totalAmount": 100}),
                        self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert "[红包]" in inbox.text and "恭喜发财" in inbox.text
    assert "金额原始值=100" in inbox.text


def test_personal_card_and_mini_program():
    card = parse_event(_event(41, {"realName": "王五", "nickname": "五哥", "corpName": "示例"}),
                       self_id=SELF_ID, self_nick=SELF_NICK)
    assert card is not None
    assert "[名片]" in card.text and "王五" in card.text and "示例" in card.text

    app = parse_event(_event(78, {"title": "订货", "desc": "在线下单", "appName": "示例商城"}),
                      self_id=SELF_ID, self_nick=SELF_NICK)
    assert app is not None
    assert "[小程序]" in app.text and "订货" in app.text and "示例商城" in app.text


def test_channel_solitaire_and_room_notice():
    channel = parse_event(_event(141, {"channelName": "新品演示",
                                       "channelUrl": "https://channels.weixin.qq.com/x"}),
                          self_id=SELF_ID, self_nick=SELF_NICK)
    assert channel is not None and "[视频号]" in channel.text and "新品演示" in channel.text

    solitaire = parse_event(_event(213, {"title": "周五团建接龙"}),
                            self_id=SELF_ID, self_nick=SELF_NICK)
    assert solitaire is not None and solitaire.text == "[接龙] 周五团建接龙"

    notice = parse_event(_event(570, {"innerRoomNotice": {"content": "周会改到 15:00"}}),
                         self_id=SELF_ID, self_nick=SELF_NICK)
    assert notice is not None and notice.text == "[群通知] 周会改到 15:00"


def test_notification_card_prefers_text_and_json_title():
    plain = parse_event(_event(573, {"notifyTitle": "审批", "notifyMsgDesc": "待你处理",
                                     "notifyContent": "报销单 123"}),
                        self_id=SELF_ID, self_nick=SELF_NICK)
    assert plain is not None
    assert "[通知]" in plain.text and "审批" in plain.text and "报销单 123" in plain.text

    rich = parse_event(_event(573, {"notifyRichContent": '{"header":{"title":"抄送提醒"}}'}),
                       self_id=SELF_ID, self_nick=SELF_NICK)
    assert rich is not None and "抄送提醒" in rich.text


def test_tips_and_member_operations():
    tips = parse_event(_event(1022, {"subType": 1,
                                     "tipsList": [{"tips": "群设置已更新", "vids": []},
                                                  {"tips": "张三 加入群聊", "vids": [123]}]}),
                       self_id=SELF_ID, self_nick=SELF_NICK)
    assert tips is not None
    assert "群设置已更新" in tips.text and "张三 加入群聊" in tips.text

    op = parse_event(_event(1043, {"type": 2, "opVid": 1, "vids": [11, 12]}),
                     self_id=SELF_ID, self_nick=SELF_NICK)
    assert op is not None and "type=2" in op.text and "2 人" in op.text

    changed = parse_event(_event(1003, {"changedMemberList": "张三、李四"}),
                          self_id=SELF_ID, self_nick=SELF_NICK)
    assert changed is not None and "张三、李四" in changed.text


def test_security_center_wording_for_both_types():
    for msg_type in (503, 2166):
        inbox = parse_event(_event(msg_type, {"wording": "邀请你加入通话"}),
                            self_id=SELF_ID, self_nick=SELF_NICK)
        assert inbox is not None and inbox.text == "[通话] 邀请你加入通话"


def test_decoded_raw_data_fallback():
    data = {"base64RawData": "eA==", "decodedRawData": {"format": "utf8", "text": "原始正文"}}
    inbox = parse_event(_event(1023, data), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None and inbox.text == "原始正文"


def test_unknown_but_parsed_type_gets_placeholder():
    item = _event(2314, {"serviceIdList": [1, 2]})
    item["display"]["parseStatus"] = "unsupported"
    item["display"]["messageType"] = {"msgType": 2314, "known": True,
                                      "nativeName": "kServiceList"}
    inbox = parse_event(item, self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None
    assert inbox.text == "[kServiceList]"
    assert inbox.msg_name == "kServiceList"


def test_empty_body_unknown_type_still_dropped():
    item = _event(2314, {})
    item["display"]["parseStatus"] = "empty_body"
    item["display"]["messageType"] = {"msgType": 2314, "known": True,
                                      "nativeName": "kServiceList"}
    assert parse_event(item, self_id=SELF_ID, self_nick=SELF_NICK) is None
