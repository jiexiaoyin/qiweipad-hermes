"""入站媒体：下载 → 解码 → 落到 Hermes 媒体缓存，返回本地路径。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import struct
import tempfile
from pathlib import Path
from typing import Optional

from gateway.platforms.base import (
    cache_audio_from_bytes_async,
    cache_document_from_bytes_async,
    cache_image_from_bytes_async,
    cache_video_from_bytes_async,
)

from .client import QWPadClient, QWPadError
from .inbound import MediaRef

logger = logging.getLogger(__name__)

# silk 解码器（与 wpp 插件同一套二进制；可用 QWPAD_SILK_DECODER 覆盖）
DEFAULT_SILK_DECODER = str(Path.home() / "silk_decoder" / "silk" / "decoder")
SILK_DECODER_PATH = os.environ.get("QWPAD_SILK_DECODER") or DEFAULT_SILK_DECODER
# 出站语音：微信只认 SILK；链路 ffmpeg(24k mono) → encoder。
DEFAULT_SILK_ENCODER = str(Path.home() / "silk_decoder" / "silk" / "encoder")
SILK_ENCODER_PATH = os.environ.get("QWPAD_SILK_ENCODER") or DEFAULT_SILK_ENCODER
FFMPEG_PATH = os.environ.get("QWPAD_FFMPEG") or "ffmpeg"
ENCODE_TIMEOUT_S = 30.0
# 厂商语音气泡上限（wpp 实测 60 KB；留余量，超限依次降码率）。
MAX_SILK_BYTES = 60_000
_SILK_RATES = (None, 12_000, 8_000, 6_000)
DECODE_TIMEOUT_S = 15.0
WAV_SAMPLE_RATE = 24000
WAV_CHANNELS = 1
WAV_BITS = 16
MAX_MEDIA_BYTES = 50 * 1024 * 1024

_MEDIA_TYPES = {
    "image": "image/jpeg",
    "voice": "audio/wav",
    "video": "video/mp4",
    "file": "application/octet-stream",
}


def build_wav_buffer(pcm: bytes) -> bytes:
    """PCM → WAV（44 字节 RIFF 头）。字段写错只会表现为「转写是乱码」。"""
    byte_rate = WAV_SAMPLE_RATE * WAV_CHANNELS * (WAV_BITS // 8)
    block_align = WAV_CHANNELS * (WAV_BITS // 8)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, WAV_CHANNELS,
                                    WAV_SAMPLE_RATE, byte_rate, block_align, WAV_BITS)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


async def decode_silk(silk: bytes, decoder: str = "") -> Optional[bytes]:
    """SILK → WAV。解码器缺失/失败返回 ``None``（调用方降级成文件）。"""
    decoder = decoder or SILK_DECODER_PATH
    if not os.path.isfile(decoder) or not os.access(decoder, os.X_OK):
        logger.debug("QWPad: silk 解码器不可用 %s，语音按文件处理", decoder)
        return None
    silk_path = pcm_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".silk", delete=False) as fh:
            fh.write(silk)
            silk_path = fh.name
        pcm_path = silk_path + ".pcm"
        proc = await asyncio.create_subprocess_exec(
            decoder, silk_path, pcm_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), timeout=DECODE_TIMEOUT_S)
        if proc.returncode != 0 or not os.path.isfile(pcm_path):
            logger.warning("QWPad: silk 解码失败 rc=%s %s", proc.returncode, (err or b"")[:200])
            return None
        pcm = Path(pcm_path).read_bytes()
        return build_wav_buffer(pcm) if pcm else None
    except Exception as exc:  # noqa: BLE001 - 解码失败不能冒泡成「消息处理失败」
        logger.warning("QWPad: silk 解码异常: %s", exc)
        return None
    finally:
        for path in (silk_path, pcm_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


async def encode_voice_to_silk(audio_path: str) -> Optional[tuple[bytes, int]]:
    """任意音频 → ``(silk_bytes, voice_time_seconds)``；不可用/失败返回 ``None``。

    ``voice_time`` 按实际 PCM 时长向上取整（厂商要求秒数，填 0 会被拒收）。
    """
    if not audio_path or not os.path.isfile(audio_path):
        return None
    if not (os.path.isfile(SILK_ENCODER_PATH) and os.access(SILK_ENCODER_PATH, os.X_OK)):
        logger.debug("QWPad: silk 编码器不可用 %s", SILK_ENCODER_PATH)
        return None
    pcm_path = silk_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as fh:
            pcm_path = fh.name
        silk_path = pcm_path + ".silk"
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_PATH, "-y", "-loglevel", "error", "-i", audio_path,
            "-ar", "24000", "-ac", "1", "-f", "s16le", pcm_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), timeout=ENCODE_TIMEOUT_S)
        if proc.returncode != 0 or not os.path.isfile(pcm_path):
            logger.warning("QWPad: ffmpeg 转 PCM 失败: %s", (err or b"")[:200])
            return None
        pcm_size = os.path.getsize(pcm_path)
        if pcm_size <= 0:
            return None
        for bitrate in _SILK_RATES:
            cmd = [SILK_ENCODER_PATH, pcm_path, silk_path, "-tencent", "-quiet"]
            if bitrate:
                cmd += ["-rate", str(bitrate)]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), timeout=ENCODE_TIMEOUT_S)
            if proc.returncode != 0 or not os.path.isfile(silk_path):
                logger.warning("QWPad: silk 编码失败: %s", (err or b"")[:200])
                return None
            silk = Path(silk_path).read_bytes()
            if silk.startswith(b"\x02") and len(silk) <= MAX_SILK_BYTES:
                # 24kHz mono s16 ⇒ 48 字节/毫秒；向上取整到秒。
                voice_time = max(1, math.ceil(pcm_size / 48 / 1000))
                return silk, voice_time
            logger.info("QWPad: silk %s 字节超限（rate=%s），降码率重试", len(silk), bitrate)
            with contextlib.suppress(OSError):
                os.unlink(silk_path)
        return None
    except Exception as exc:  # noqa: BLE001 - 编码失败由调用方降级
        logger.warning("QWPad: silk 编码异常: %s", exc)
        return None
    finally:
        for path in (pcm_path, silk_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


async def cache_media(client: QWPadClient, ref: MediaRef) -> Optional[tuple[str, str]]:
    """下载并缓存一条媒体，返回 ``(local_path, media_type)``；失败 ``None``。"""
    try:
        if ref.wx_candidates:
            raw = await client.download_wx_file(ref.as_dict())
        else:
            raw = await client.download_wxwork_file(ref.as_dict())
    except QWPadError as exc:
        logger.warning("QWPad: 下载%s失败 fileId=%s: %s", ref.kind, ref.file_id, exc)
        return None
    if not raw:
        return None
    if len(raw) > MAX_MEDIA_BYTES:
        logger.warning("QWPad: %s 超过大小上限 %s 字节，丢弃", ref.kind, len(raw))
        return None

    try:
        if ref.kind == "image":
            return await cache_image_from_bytes_async(raw, ".jpg"), "image/jpeg"
        if ref.kind == "video":
            return await cache_video_from_bytes_async(raw, ".mp4"), "video/mp4"
        if ref.kind == "voice":
            wav = await decode_silk(raw)
            if wav:
                return await cache_audio_from_bytes_async(wav, ".wav"), "audio/wav"
            return await cache_document_from_bytes_async(raw, ref.filename or "voice.silk"), "application/octet-stream"
        name = ref.filename or "file"
        return await cache_document_from_bytes_async(raw, name), "application/octet-stream"
    except Exception as exc:  # noqa: BLE001 - 缓存失败只是少一个附件
        logger.warning("QWPad: 缓存%s失败: %s", ref.kind, exc)
        return None
