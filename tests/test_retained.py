"""非聊天事件留存单测 —— 用 live 抓到的 unified 事件形状当夹具。"""

from __future__ import annotations

import json
import os
import stat
import time

from qwpad.retained import (
    EventClass,
    RetentionPolicy,
    RetentionStore,
    build_record,
    classify_event,
    extract_card,
)

SELF_ID = "1688850000000001"


def _event(
    msg_type,
    *,
    category="unknown",
    event_type=15000,
    data=None,
    display_text="",
    title="",
    native_name=None,
    sender="",
    room="0",
    timestamp=1792628756,
    event_id=1001,
) -> dict:
    return {
        "id": event_id,
        "createdAt": "2026-10-22 00:25:57",
        "type": event_type,
        "display": {
            "category": category,
            "text": display_text,
            "senderId": sender,
            "senderName": "",
            "roomId": room,
            "messageId": "MSG-1",
            "messageType": {"msgType": msg_type, "known": True, "nativeName": native_name},
            "title": title,
            "content": None,
        },
        "message": {
            "msgType": msg_type,
            "msgData": data,
            "structured": {
                "senderId": sender,
                "senderName": "",
                "fromRoomId": room,
                "receiverId": SELF_ID,
                "msgType": msg_type,
                "msgData": data,
                "msgUniqueIdentifier": "MSG-1",
                "isRevoked": False,
                "timestamp": timestamp,
                "parseStatus": "partial",
            },
        },
    }


def _chat_event() -> dict:
    return _event(0, category="chat", data={"content": "你好"}, sender="1688851111111111")


def _friend_apply() -> dict:
    return _event(
        2357,
        category="system",
        event_type=15500,
        sender="10030",
        room="10030",
        title="联系人申请",
        display_text="测试用户",
        native_name="E_CONTENT_FRIEND_APPLY_OTHER_CORP",
        data={
            "applyTime": 1792628756,
            "contactApply": {
                "contactId": "7881300000000001",
                "contactNickname": "测试用户",
                "contactType": "微信",
                "userId": "1970320000000002",
            },
            "contactId": "7881300000000001",
            "contactNickname": "测试用户",
            "contactType": "微信",
            "corpId": "0",
            "userId": "7881300000000001",
            "extras": None,
        },
    )


def _approval() -> dict:
    card = {
        "main_title": {"title": "📋 企业微信审批结果", "sub_title": "✅通过 · 发票"},
        "card_desc": "审批进度: ✅ 朱其霞 → ✅ 李笑玲",
    }
    decoded = "\n\x1d📋 企业微信审批结果\x12\x80\x11" + json.dumps(card, ensure_ascii=False)
    return _event(
        145,
        category="unknown",
        event_type=15000,
        native_name="E_CONTENT_FUNVIEW_UNION",
        data={"base64RawData": "", "decodedRawData": {"text": decoded}, "extras": None},
    )


def _cc() -> dict:
    card = {
        "main_title": {"title": "高维芳抄送了发票申请给你"},
        "horizon_sub_title_list": [
            {"sub_title": "发票类型：普票"},
            {"sub_title": "开票金额：11700.00元"},
        ],
    }
    decoded = "\n$高维芳抄送了发票申请给你\x12\x90\x0c" + json.dumps(card, ensure_ascii=False)
    return _event(
        561,
        category="unknown",
        event_type=15500,
        native_name="E_CONTENT_FUN_VIEW_DATA_TIPS",
        data={"decodedRawData": {"text": decoded}, "extras": None},
    )


def _friend_changed() -> dict:
    return _event(
        2131,
        category="unknown",
        event_type=15500,
        sender="10030",
        room="10030",
        native_name="kFriendChanged",
        data={"extras": None},
    )


def _friend_apply_signal() -> dict:
    return _event(
        2132,
        category="unknown",
        event_type=15500,
        sender="10030",
        room="10030",
        native_name="kFriendApply",
        data={"extras": None},
    )


def _text_control() -> dict:
    return _event(
        1011,
        category="unknown",
        event_type=15000,
        native_name="kTextControl",
        data={"decodedRawData": {"text": "你已添加了测试用户，现在可以开始聊天了。"}, "extras": None},
    )


def _read_receipt() -> dict:
    return _event(2001, category="unknown", event_type=15000, native_name="kReportRead",
                  data={"extras": None})


def _unknown_empty() -> dict:
    return _event(2180, category="unknown", event_type=15500, native_name=None,
                  data={"extras": None})


def _account_event() -> dict:
    return _event(
        0,
        category="unknown",
        event_type=11004,
        data=None,
        title="通知（事件 11004）",
    )


def _app_card() -> dict:
    return _event(
        38,
        category="chat",
        event_type=15000,
        sender="5629500000000001",
        native_name="kAppTextCard",
        data={"title": "提醒：请及时配置「个人欢迎语」", "content": "客户名：测试用户"},
    )


def test_classify_observed_events():
    cases = [
        (_friend_apply(), EventClass("retain", "friend_apply")),
        (_friend_apply_signal(), EventClass("retain", "friend_apply")),
        (_friend_changed(), EventClass("retain", "friend_change")),
        (_approval(), EventClass("retain", "approval")),
        (_cc(), EventClass("retain", "cc")),
        (_text_control(), EventClass("retain", "relationship")),
        (_app_card(), EventClass("retain", "app_card")),
        (_account_event(), EventClass("retain", "account_meta")),
        (_chat_event(), EventClass("chat")),
        (_read_receipt(), EventClass("drop", reason="read_receipt")),
        (_unknown_empty(), EventClass("drop", kind="other")),
    ]
    for item, expected in cases:
        got = classify_event(item)
        assert (got.action, got.kind) == (expected.action, expected.kind), (item["id"], got)


def test_policy_off_disables_non_chat_but_never_chats():
    policy = RetentionPolicy.from_value("off")
    assert classify_event(_friend_apply(), policy).action == "drop"
    assert classify_event(_app_card(), policy).action == "drop"
    assert classify_event(_chat_event(), policy).action == "chat"


def test_policy_custom_kinds():
    policy = RetentionPolicy.from_value("approval,cc")
    assert classify_event(_approval(), policy).action == "retain"
    assert classify_event(_cc(), policy).action == "retain"
    assert classify_event(_friend_apply(), policy).action == "drop"


def test_policy_all_retains_unknown_non_chat_but_not_noise():
    policy = RetentionPolicy.from_value("all")
    assert classify_event(_unknown_empty(), policy).action == "retain"
    assert classify_event(_unknown_empty(), policy).kind == "other"
    assert classify_event(_read_receipt(), policy).action == "drop"


def test_build_record_friend_apply_keeps_contact_identity():
    record = build_record(_friend_apply(), "friend_apply")
    assert record["kind"] == "friend_apply"
    assert record["text"] == "测试用户"
    assert record["data"]["contactId"] == "7881300000000001"
    assert record["data"]["contactNickname"] == "测试用户"
    assert record["raw"]["id"] == 1001


def test_build_record_approval_extracts_card():
    record = build_record(_approval(), "approval")
    assert record["card"]["main_title"]["sub_title"] == "✅通过 · 发票"
    assert "审批结果" in record["text"]


def test_build_record_cc_extracts_invoice_fields():
    record = build_record(_cc(), "cc")
    assert record["card"]["main_title"]["title"] == "高维芳抄送了发票申请给你"
    assert any("11700" in entry["sub_title"]
               for entry in record["card"]["horizon_sub_title_list"])


def test_build_record_text_control_decodes():
    record = build_record(_text_control(), "relationship")
    assert record["text"] == "你已添加了测试用户，现在可以开始聊天了。"
    assert record["card"] is None


def test_build_record_account_meta_omits_body():
    record = build_record(_account_event(), "account_meta")
    assert record["metaOnly"] is True
    assert record["text"] == ""
    assert "raw" not in record
    assert record["type"] == 11004


def test_extract_card_ignores_invalid_json():
    assert extract_card("no json here") is None
    assert extract_card("{bad json") is None
    assert extract_card('prefix {"ok": true}') == {"ok": True}


def test_store_append_read_and_permissions(tmp_path):
    store = RetentionStore("token", directory=tmp_path, retention_days=0)
    store.append(_friend_apply(), "friend_apply")
    store.append(_approval(), "approval")

    records = store.read()
    assert [r["kind"] for r in records] == ["friend_apply", "approval"]
    assert store.read(kind="approval")[0]["msgType"] == 145
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_store_rotation_keeps_readable(tmp_path):
    store = RetentionStore("token", directory=tmp_path, max_bytes=64 * 1024,
                           keep=2, retention_days=0)
    item = _event(0, category="chat", data={"content": "x" * 40000}, sender="u")
    store.append(item, "friend_apply")
    store.append(item, "friend_apply")
    store.append(item, "friend_apply")

    assert store._rotated(1).exists()
    assert len(store.read(limit=3)) == 3


def test_store_prunes_expired_records(tmp_path):
    store = RetentionStore("token", directory=tmp_path, retention_days=1)
    old = _event(0, category="chat", data={"content": "old"}, sender="u",
                 display_text="old", timestamp=int(time.time()) - 3 * 86400)
    new = _event(0, category="chat", data={"content": "new"}, sender="u",
                 display_text="new", timestamp=int(time.time()))
    store.append(old, "friend_apply")
    store.append(new, "friend_apply")

    store._last_prune = 0.0
    store._maybe_prune()
    records = store.read()
    assert [r["text"] for r in records] == ["new"]
