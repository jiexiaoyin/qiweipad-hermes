"""个人微信图片下载：big → middle → thumb 依次回退。"""

from __future__ import annotations

import asyncio

from qwpad.client import QWPadClient, QWPadError

CANDIDATES = [
    {"fileUrl": "https://imunion.weixin.qq.com/big", "fileSize": 1213216, "fileType": 1},
    {"fileUrl": "https://imunion.weixin.qq.com/mid", "fileSize": 39783, "fileType": 2},
    {"fileUrl": "https://imunion.weixin.qq.com/thumb", "fileSize": 3067, "fileType": 3},
]


def _client(*, failing_types, download_error=False):
    client = QWPadClient("wxa_test")
    posts: list[dict] = []
    downloads: list[str] = []

    async def fake_post(path, payload=None, **kwargs):
        assert path == "/cloud/wxDownload"
        posts.append(payload)
        if payload["fileType"] in failing_types:
            raise QWPadError(f"{path}: HTTP 502", status=502)
        return {"cloudUrl": f"https://api.qwpad.com/cloud/files?k={payload['fileType']}"}

    async def fake_download(url, max_bytes=None):
        downloads.append(url)
        if download_error:
            raise QWPadError("下载失败")
        return b"\xff\xd8thumbnail"

    client.post = fake_post          # type: ignore[method-assign]
    client._download_url = fake_download  # type: ignore[method-assign]
    return client, posts, downloads


def _ref():
    return {"aesKey": "AES", "fileAuthKey": "AUTH", "wxCandidates": CANDIDATES}


def test_falls_back_to_thumbnail_when_big_and_middle_fail():
    client, posts, downloads = _client(failing_types={1, 2})

    raw = asyncio.run(client.download_wx_file(_ref()))

    assert raw == b"\xff\xd8thumbnail"
    assert [p["fileType"] for p in posts] == [1, 2, 3]
    assert all(p["fileAuthkey"] == "AUTH" and p["fileAeskey"] == "AES" for p in posts)
    assert downloads == ["https://api.qwpad.com/cloud/files?k=3"]


def test_big_image_wins_when_available():
    client, posts, downloads = _client(failing_types=set())

    raw = asyncio.run(client.download_wx_file(_ref()))

    assert raw and len(downloads) == 1
    assert [p["fileType"] for p in posts] == [1]


def test_all_sizes_failed_returns_none():
    client, posts, _ = _client(failing_types={1, 2, 3})

    assert asyncio.run(client.download_wx_file(_ref())) is None
    assert [p["fileType"] for p in posts] == [1, 2, 3]


def test_no_candidates_returns_none():
    client, posts, _ = _client(failing_types=set())
    assert asyncio.run(client.download_wx_file({"wxCandidates": []})) is None
    assert posts == []
