#!/usr/bin/env python3
"""司凤音造局：Suno API 驱动的中文音乐改编工作台。"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
OUTPUT_DIR = ROOT / "outputs"
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"
CONFIG_PATH = Path.home() / ".suno-api" / "config.json"
DEFAULT_BASE_URL = "https://www.suno-api.io"
MODEL = "suno-v6"
MODELS = ("suno-v6", "suno-v6-wild", "suno-v6-mini")
MODEL_INFO = (
    {"id": "suno-v6", "label": "Suno v6", "note": "标准版，推荐日常使用"},
    {"id": "suno-v6-wild", "label": "Suno v6 Wild", "note": "更自由、变化更大"},
    {"id": "suno-v6-mini", "label": "Suno v6 Mini", "note": "轻量版，适合快速尝试"},
)
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL = 0.0
_RATE_LIMIT_TEXT = ""
_ACCOUNT_CACHE_LOCK = threading.Lock()
_BALANCE_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_PRICE_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
ACCOUNT_CACHE_TTL = 45.0
SIFENG_STYLE = (
    "Chinese EDM, 128 BPM four-on-the-floor, bright synth-pluck arpeggios, synth brass, "
    "sidechained bass, punchy snare, crisp hi-hats, huge chorus drop, rising sweep, "
    "gruff smoky male baritone, stacked vocal doubles, raw chest-voice ad-libs, "
    "screaming WOO shouts between phrases, jazz scat singing, rapid scat runs, melisma, "
    "R&B vocal runs, guttural growls, vocal fry, beatboxing, tongue rolls, "
    "call-and-response, hype man ad-libs, dead-serious theatrical delivery of absurd lyrics, "
    "slightly off-key, wild nonsense shouts, cheap club energy, unhinged, comedic, not polished"
)
SIFENG_ADLIBS = ("(Woo!)", "(Yeah!)", "(Uh!)", "(Ho!)")
SIFENG_SCAT_LINES = (
    "ba-da-ba-da-ba (Woo!)",
    "skrrt skrrt (Uh!)",
    "ya-ya-ya-ya (Hey!)",
    "brrr-ra-ta-ta (Ho!)",
    "shoo-bee-doo-wop (Woo!)",
    "beng-ba-beng-ba (Woo!)",
    "邦吧邦吧邦吧 (Ho!)",
    "乌鲁鲁 乌鲁鲁 (Yeah!)",
    "(WOO!!!)",
)
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
OUTPUT_EXTENSIONS = {".mp3", ".wav"}
MAX_AUDIO_BYTES = 50 * 1024 * 1024
MAX_VOICE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 80 * 1024 * 1024
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
STATUS_ALIASES = {
    "created": "queued",
    "queued": "queued",
    "submitted": "submitted",
    "pending": "pending",
    "preparing_source": "preparing_source",
    "waiting_account": "waiting_account",
    "processing": "processing",
    "playable": "playable",
    "streaming": "streaming",
    "complete": "completed",
    "completed": "completed",
    "success": "completed",
    "succeeded": "completed",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
}


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None and item != ""}


def unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def first_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_status(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    return STATUS_ALIASES.get(value.strip().lower(), "unknown")


def selected_model(value: Any = None) -> str:
    model = first_string(value) or MODEL
    if model not in MODELS:
        raise AppError(400, "MODEL_INVALID", "不支持的模型版本，请重新选择。")
    return model


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def require_text_limit(field: str, value: str, limit: int, label: str) -> None:
    length = utf16_length(value)
    if length > limit:
        raise AppError(
            400,
            "TEXT_TOO_LONG",
            f"{label}过长：{length} / {limit} 字符，请精简后再提交。",
            {"field": field, "length": length, "limit": limit},
        )


def redact(text: str, secret: str | None = None) -> str:
    result = str(text)
    if secret:
        result = result.replace(secret, "[REDACTED]")
    return re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[REDACTED]", result)


def create_url_opener() -> urllib.request.OpenerDirector:
    proxy = os.environ.get("SIFENG_PROXY", "").strip()
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    if os.environ.get("SIFENG_USE_SYSTEM_PROXY", "").strip() == "1":
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def normalize_api_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        raise AppError(400, "API_KEY_REQUIRED", "请填写 API Key。")
    if not key.isascii() or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", key):
        raise AppError(
            400,
            "API_KEY_INVALID",
            "API Key 只能包含英文字母、数字和 - _ . ~ + / =；请不要粘贴带省略号的脱敏 Key。",
        )
    if len(key) < 10:
        raise AppError(400, "API_KEY_INVALID", "API Key 长度异常，请确认复制完整。")
    return key


def normalize_base_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url.isascii():
        raise AppError(
            400,
            "BASE_URL_INVALID",
            "Base URL 只能包含 ASCII 字符，请填写 https://www.suno-api.io。",
        )
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError(400, "BASE_URL_INVALID", "Base URL 必须以 http:// 或 https:// 开头，并包含域名。")
    return url


def load_config() -> tuple[str, str]:
    if not CONFIG_PATH.exists():
        raise AppError(503, "CONFIG_MISSING", "未找到 Suno API 配置，请先运行 Agent Kit 配置。")
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppError(503, "CONFIG_INVALID", f"配置文件无法解析：{exc}") from exc
    api_key = first_string(payload.get("apiKey") or payload.get("api_key"))
    if not api_key:
        raise AppError(503, "CONFIG_INVALID", "配置文件中没有 API Key。")
    base_url = first_string(payload.get("baseUrl") or payload.get("base_url")) or DEFAULT_BASE_URL
    return normalize_api_key(api_key), normalize_base_url(base_url)


def mask_key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) <= 10:
        return "••••••"
    return key[:4] + "…" + key[-4:]


def config_summary() -> dict[str, Any]:
    result: dict[str, Any] = {
        "configured": False,
        "base_url": DEFAULT_BASE_URL,
        "key_masked": "",
        "config_path": str(CONFIG_PATH),
    }
    if not CONFIG_PATH.exists():
        return result
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        result["error"] = "配置文件无法解析"
        return result
    api_key = first_string(payload.get("apiKey") or payload.get("api_key")) or ""
    base_url = first_string(payload.get("baseUrl") or payload.get("base_url")) or DEFAULT_BASE_URL
    result.update({
        "configured": bool(api_key),
        "base_url": base_url.rstrip("/"),
        "key_masked": mask_key(api_key),
    })
    return result


def save_config(api_key: str, base_url: str) -> None:
    key = normalize_api_key(api_key)
    url = normalize_base_url(base_url)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"apiKey": key, "baseUrl": url, "updatedAt": utc_now()}
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _add_adlib(line: str, index: int) -> str:
    clean = str(line or "").strip()
    if not clean:
        return clean
    if re.fullmatch(r"\[[^\]]+\]", clean):
        return clean
    if re.fullmatch(r"[（(][^()（）]*[)）]", clean):
        return clean
    if re.search(r"\b(?:woo+|yeah|uh|ho|hey|skrrt|brrr|beng)\b|邦吧|乌鲁鲁", clean, re.I):
        return clean
    return f"{clean} {SIFENG_ADLIBS[index % len(SIFENG_ADLIBS)]}"


def _scream_adlibs(line: str) -> str:
    text = str(line or "").strip()
    if not text:
        return text

    def replace_parenthetical(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if re.fullmatch(r"(?:WOO+|Yeah|Uh|Ho|Hey)[!！]*", inner, re.I):
            return f"[scream] ({inner})"
        return match.group(0)

    text = re.sub(r"(?<!\[scream\] )\(([^()]*)\)", replace_parenthetical, text)
    parts = re.split(r"([()])", text)
    depth = 0
    output: list[str] = []
    for part in parts:
        if part == "(":
            depth += 1
            output.append(part)
            continue
        if part == ")":
            depth = max(0, depth - 1)
            output.append(part)
            continue
        if depth == 0:
            part = re.sub(
                r"(?<!\[scream\] )\b(WOO+|Yeah|Uh|Ho|Hey)\b",
                r"[scream] \1",
                part,
                flags=re.I,
            )
        output.append(part)
    return "".join(output)


def _section_lines(name: str, lines: list[str], offset: int = 0) -> tuple[list[str], int]:
    output = [f"[{name}]"]
    for position, line in enumerate(lines):
        output.append(_add_adlib(line, offset + position))
    return output, offset + len(lines)


def refine_sifeng_lyrics(lyrics: str, title: str = "") -> dict[str, Any]:
    text = str(lyrics or "").replace("\r\n", "\n").strip()
    if not text:
        raise AppError(400, "LYRICS_REQUIRED", "请先粘贴或上传歌词。")
    raw_lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(raw_lines) < 2:
        raise AppError(400, "LYRICS_TOO_SHORT", "至少提供 2 行歌词。")
    has_sections = any(re.fullmatch(r"\[[^\]]+\]", line) for line in raw_lines)
    notes: list[str] = []

    if has_sections:
        output: list[str] = []
        adlib_index = 0
        has_scat = any(re.search(r"scat|口技", line, re.I) for line in raw_lines)
        for line in raw_lines:
            if re.fullmatch(r"\[[^\]]+\]", line):
                output.append(line)
            else:
                output.append(_add_adlib(line, adlib_index))
                adlib_index += 1
        if not has_scat:
            insert_at = len(output)
            for position in range(len(output) - 1, -1, -1):
                if re.fullmatch(r"\[[^\]]+\]", output[position]):
                    insert_at = position
                    break
            output[insert_at:insert_at] = ["[Scat Break]", *SIFENG_SCAT_LINES, ""]
            notes.append("已插入 Scat Break 口技段")
        notes.append("已保留原段落结构并加入句间 ad-lib")
    else:
        verse1 = raw_lines[:4]
        rest = raw_lines[4:]
        pre = rest[:2]
        rest = rest[2:]
        drop_lines = rest[:4] or verse1[-2:]
        rest = rest[4:]

        output = ["[Intro]", "(呼——哈——)", "(邦吧邦吧邦吧)", "(WOO!!!)", ""]
        section, cursor = _section_lines("Verse 1", verse1, 0)
        output.extend(section)
        output.append("")
        if pre:
            section, cursor = _section_lines("Pre-Chorus", pre, cursor)
            output.extend(section)
        else:
            output.append("[Pre-Chorus]")
            notes.append("歌词较短，已自动补齐 Pre-Chorus")
        output.extend(["", "(乌鲁鲁 乌鲁鲁)", "(WOO!!!)", ""])
        section, cursor = _section_lines("Drop", drop_lines, cursor)
        output.extend(section)
        output.extend(["", "[Scat Break]", *SIFENG_SCAT_LINES, ""])
        if rest:
            section, cursor = _section_lines("Verse 2", rest[:6], cursor)
            output.extend(section)
            output.append("")
        else:
            output.extend([
                "[Bridge]",
                "你说人生 是一场修行 (Woo!)",
                "我说人生 是一碗隔夜面 (Yeah!)",
                "热一热 还能吃 (Uh!)",
                "加个蛋 就圆满 (Ho!)",
                "",
            ])
            notes.append("已补一段 Bridge 歌词")
        section, cursor = _section_lines("Final Drop", drop_lines, cursor)
        output.extend(section)
        output.extend(["", "[Outro]", "(呼——哈——)", "(WOO!!!)", "(通透!)", "(邦吧邦吧邦吧)", "(乌鲁鲁 乌鲁鲁)", "(收!)"])
        notes.insert(0, "已重组为 Intro / Verse / Drop / Scat Break / Outro")
        notes.append("已按句加入 Woo / Yeah / Uh / Ho ad-lib")

    output = [_scream_adlibs(line) for line in output]
    return {
        "lyrics": "\n".join(output).strip(),
        "title": str(title or "").strip() or "司凤原创",
        "notes": notes,
    }


def draft_sifeng_lyrics(idea: str) -> str:
    theme = str(idea or "").strip() or "通透"
    theme = re.sub(r"^(请|帮我|写一首|写个|做一首|来一首|关于)\s*", "", theme).strip()
    short = re.sub(r"\s+", " ", theme)[:30] or "通透"
    lines = [
        "凌晨三点 我脑子里冒出一句话",
        f"「{short}」",
        "我坐起来 又躺回去",
        "这事值得明天再说",
        "镜子里的男人 对我点了点头",
        "他说兄弟 你已经很通透了",
        "我把烦恼 折成纸飞机",
        "从六楼扔出去 又下楼捡回来",
        "不是舍不得 是环保",
        "通透 通透 我悟了但没完全悟",
        "邦吧邦吧邦吧 我的心像不锈钢",
        "算了算了都算了",
        "WOO! 明天再说吧",
    ]
    return refine_sifeng_lyrics("\n".join(lines), "悟了，但没完全悟")["lyrics"]


def extract_lyrics_text(value: Any) -> str:
    preferred = ("data", "lyrics", "text", "content", "result", "output")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [extract_lyrics_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in preferred:
            if key in value:
                text = extract_lyrics_text(value[key])
                if text:
                    return text
        for item in value.values():
            text = extract_lyrics_text(item)
            if text:
                return text
    return ""


def safe_filename(name: str, fallback: str = "audio") -> str:
    base = Path(name or "").name.strip() or fallback
    base = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", base, flags=re.UNICODE)
    base = base.strip("._-") or fallback
    return base[:80]


def decode_audio(file_name: str, encoded: str, voice: bool = False) -> tuple[str, bytes]:
    name = safe_filename(file_name, "voice" if voice else "song")
    suffix = Path(name).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise AppError(400, "AUDIO_TYPE", "不支持该音频格式，请使用 MP3、WAV、M4A、FLAC、OGG 或 AAC。")
    text = encoded.strip()
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError(400, "AUDIO_BASE64", "音频数据无效，请重新选择文件。") from exc
    limit = MAX_VOICE_BYTES if voice else MAX_AUDIO_BYTES
    if len(data) < 512:
        raise AppError(400, "AUDIO_EMPTY", "音频文件为空或过小。")
    if len(data) > limit:
        label = "音色样本" if voice else "源音频"
        raise AppError(400, "AUDIO_TOO_LARGE", f"{label}超过 {limit // 1024 // 1024} MB 限制。")
    return name, data


def find_url(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("url", "audio_url", "wav_url", "download_url", "file_url"):
            candidate = first_string(value.get(key))
            if candidate and candidate.startswith(("http://", "https://")):
                return candidate
        for item in value.values():
            found = find_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_url(item)
            if found:
                return found
    return None

def normalize_song(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    song_id = first_string(value.get("song_id") or value.get("id") or value.get("clip_id"))
    if not song_id:
        return None
    title = first_string(value.get("song_title") or value.get("title") or value.get("name"))
    audio = first_string(value.get("audio_url") or value.get("url") or value.get("stream_url"))
    image = first_string(value.get("image_url") or value.get("cover_url") or value.get("image"))
    return {
        "song_id": song_id,
        "title": title or "未命名作品",
        "status": normalize_status(value.get("status")),
        "audio_url": audio,
        "image_url": image,
        "wav_url": first_string(value.get("wav_url")),
        "video_url": first_string(value.get("video_url")),
        "duration": value.get("duration"),
        "lyrics": first_string(value.get("lyrics")),
        "style_tags": first_string(value.get("style_tags") or value.get("tags")),
        "error_message": first_string(value.get("error_message") or value.get("error")),
        "raw": value,
    }


def collect_songs(value: Any, output: list[dict[str, Any]], seen: set[str]) -> None:
    if isinstance(value, dict):
        song = normalize_song(value)
        if song and song["song_id"] not in seen:
            seen.add(song["song_id"])
            output.append(song)
        for item in value.values():
            collect_songs(item, output, seen)
    elif isinstance(value, list):
        for item in value:
            collect_songs(item, output, seen)


def normalize_submission(payload: Any) -> dict[str, Any]:
    data = unwrap(payload)
    record = data if isinstance(data, dict) else {}
    request_id = first_string(record.get("request_id") or record.get("client_request_id"))
    songs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("songs", "clips", "items", "results", "tracks", "tasks"):
        collect_songs(record.get(key), songs, seen)
    if not songs:
        collect_songs(data, songs, seen)
    raw_tasks = record.get("tasks")
    tasks: list[dict[str, Any]] = []
    if isinstance(raw_tasks, list):
        for index, item in enumerate(raw_tasks):
            if not isinstance(item, dict):
                continue
            tasks.append({
                "index": item.get("index", item.get("batch_index", index)),
                "status": normalize_status(item.get("status")),
                "song_id": first_string(item.get("song_id")),
                "title": first_string(item.get("title") or item.get("song_title")),
                "error_message": first_string(item.get("error_message")),
            })
    if not tasks and songs:
        tasks = [
            {
                "index": index,
                "status": song.get("status") or "unknown",
                "song_id": song.get("song_id"),
                "title": song.get("title"),
                "error_message": song.get("error_message"),
            }
            for index, song in enumerate(songs)
        ]
    status = normalize_status(record.get("status"))
    if status == "unknown":
        states = [task.get("status") for task in tasks]
        if states and all(state == "completed" for state in states):
            status = "completed"
        elif any(state == "failed" for state in states):
            status = "failed"
        elif states:
            status = "processing"
        else:
            status = "queued"
    return {
        "request_id": request_id,
        "status": status,
        "tasks": tasks,
        "songs": songs,
        "raw": payload,
    }


def normalize_voice(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    voice_id = first_string(value.get("voice_id") or value.get("id") or value.get("persona_id"))
    if not voice_id:
        return None
    return {
        "voice_id": voice_id,
        "title": first_string(value.get("title") or value.get("name")) or "未命名音色",
        "status": value.get("status") or "unknown",
        "progress": value.get("progress_percent"),
        "message": first_string(value.get("progress_message") or value.get("error_message")),
        "created_at": value.get("created_at"),
    }


class SunoApi:
    def __init__(self) -> None:
        self.api_key, self.base_url = load_config()

    def _request(
        self,
        path: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        timeout: int = 75,
        binary: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        global _RATE_LIMIT_UNTIL, _RATE_LIMIT_TEXT
        with _RATE_LIMIT_LOCK:
            remaining = _RATE_LIMIT_UNTIL - time.monotonic()
            limit_text = _RATE_LIMIT_TEXT
        if remaining > 0:
            wait_seconds = max(1, int(remaining + 0.999))
            base_message = limit_text or "Suno API 请求过于频繁（429）。"
            raise AppError(429, "SUNO_RATE_LIMITED", f"{base_message} 还需等待约 {wait_seconds} 秒。")
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "*/*" if binary else "application/json",
            "User-Agent": "SifengMusicStudio/0.1",
        }
        if extra_headers:
            headers.update(extra_headers)
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            with create_url_opener().open(request, timeout=timeout) as response:
                raw = response.read()
                content_type = response.headers.get("content-type", "application/octet-stream")
        except UnicodeEncodeError as exc:
            raise AppError(
                400,
                "REQUEST_ENCODING",
                "API 配置包含中文或特殊字符，请检查 API Key 和 Base URL。",
            ) from exc
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            message = self._error_message(raw).strip()
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                wait_seconds = 60
                if retry_after:
                    try:
                        wait_seconds = max(1, int(float(retry_after)))
                    except (TypeError, ValueError):
                        pass
                message = "Suno API 请求过于频繁（429），请等待约 1 分钟再试。"
                if retry_after:
                    message += f" 平台建议等待 {retry_after} 秒。"
                with _RATE_LIMIT_LOCK:
                    _RATE_LIMIT_UNTIL = time.monotonic() + wait_seconds
                    _RATE_LIMIT_TEXT = message
            elif not message:
                message = f"Suno API 返回 HTTP {exc.code} {exc.reason}。"
            raise AppError(exc.code, "SUNO_HTTP", redact(message, self.api_key), self._safe_json(raw)) from exc
        except urllib.error.URLError as exc:
            reason = redact(str(getattr(exc, "reason", exc)), self.api_key)
            raise AppError(502, "NETWORK_ERROR", f"连接 Suno API 失败：{reason}") from exc
        except TimeoutError as exc:
            raise AppError(504, "TIMEOUT", "Suno API 请求超时，请先检查历史任务后再重试。") from exc
        if binary:
            return {"bytes": raw, "content_type": content_type}
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except Exception as exc:
            raise AppError(502, "BAD_RESPONSE", "Suno API 返回了无法解析的数据。") from exc
        if isinstance(parsed, dict):
            if parsed.get("success") is False:
                message = self._error_message(raw) or "Suno API 拒绝了请求。"
                raise AppError(400, "SUNO_REJECTED", redact(message, self.api_key), parsed)
            code = parsed.get("code")
            if isinstance(code, int) and code not in (0, 200):
                message = self._error_message(raw) or "Suno API 返回业务错误。"
                raise AppError(400, "SUNO_REJECTED", redact(message, self.api_key), parsed)
        return parsed

    def _safe_json(self, raw: bytes) -> Any:
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _error_message(self, raw: bytes) -> str:
        payload = self._safe_json(raw)
        if isinstance(payload, dict):
            for key in ("message", "error", "msg", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("message", "error", "msg", "detail"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return raw.decode("utf-8", errors="replace")[:500]

    def balance(self, timeout: int = 75) -> dict[str, Any]:
        now = time.monotonic()
        with _ACCOUNT_CACHE_LOCK:
            cached = _BALANCE_CACHE["value"]
            if cached is not None and now - float(_BALANCE_CACHE["at"]) < ACCOUNT_CACHE_TTL:
                return cached
        payload = self._request("/api/user/balance", timeout=timeout)
        data = unwrap(payload) or {}
        user = data.get("user") or {}
        display = data.get("display") or {}
        balance = data.get("balance") or {}
        result = {
            "user_id": user.get("id"),
            "username": user.get("username"),
            "amount": balance.get("effective_amount", balance.get("user_amount")),
            "currency": display.get("amount_unit", "yuan"),
        }
        with _ACCOUNT_CACHE_LOCK:
            _BALANCE_CACHE["at"] = time.monotonic()
            _BALANCE_CACHE["value"] = result
        return result

    def prices(self, timeout: int = 75) -> dict[str, Any]:
        now = time.monotonic()
        with _ACCOUNT_CACHE_LOCK:
            cached = _PRICE_CACHE["value"]
            if cached is not None and now - float(_PRICE_CACHE["at"]) < ACCOUNT_CACHE_TTL:
                return cached
        payload = self._request("/api/pricing/catalog", timeout=timeout)
        data = unwrap(payload) or {}
        catalog = data.get("catalog") or {}
        meta = catalog.get("meta") or {}
        promo = data.get("price_promotion") or {}
        active = promo.get("active") is True and promo.get("upcoming") is not True
        promo_prices = promo.get("promotion_prices") or {}
        original = promo.get("original_prices") or {}

        def number(*values: Any, fallback: float) -> float:
            for value in values:
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    continue
                return parsed
            return fallback

        def selected(key: str, fallback: float) -> float:
            return number(promo_prices.get(key) if active else original.get(key), fallback=fallback)

        prices = {
            "generation.per_request": number(meta.get("generation_price"), selected(
                "generation.per_request", 0.6), fallback=0.6),
            "generation.max_mode_factor": number(meta.get("max_mode_factor"), fallback=2),
            "voice.generate_with_clone": number(meta.get("voice_generation_price"), selected(
                "voice.generate_with_clone", 1.2), fallback=1.2),
            "upload.enhanced": number(meta.get("enhanced_upload_price"), selected(
                "upload.enhanced", 10), fallback=10),
            "stems.two_track": number(meta.get("stem_base_price"), selected(
                "stems.two_track", 1.2), fallback=1.2),
            "stems.auto_twelve": number(meta.get("stem_auto_split_price"), selected(
                "stems.auto_twelve", 3), fallback=3),
            "download.free2_per_song": number(meta.get("free2_per_song_price"), selected(
                "download.free2_per_song", 0.2), fallback=0.2),
            "edit.full_song": number(
                (promo_prices if active else original).get("edit.full_song"), fallback=0.05),
        }
        result = {"prices": prices, "promotion_active": active, "raw": payload}
        with _ACCOUNT_CACHE_LOCK:
            _PRICE_CACHE["at"] = time.monotonic()
            _PRICE_CACHE["value"] = result
        return result

    def estimate(self, mode: str, max_mode: bool = False) -> dict[str, Any]:
        prices = self.prices()["prices"]
        if mode == "voice":
            key = "voice.generate_with_clone"
        elif mode == "stems_two":
            key = "stems.two_track"
        elif mode == "stems_twelve":
            key = "stems.auto_twelve"
        elif mode == "vocal_removal":
            key = "stems.two_track"
        else:
            key = "generation.per_request"
        base = float(prices.get(key, 0))
        factor = float(prices.get("generation.max_mode_factor", 2)) if max_mode else 1.0
        amount = round(base * factor, 2)
        return {"amount": amount, "currency": "CNY", "price_key": key, "max_mode": max_mode}

    def upload_source(self, file_name: str, data: bytes, model: str = MODEL) -> dict[str, Any]:
        model = selected_model(model)
        payload = self._request(
            "/api/music/upload-source",
            "POST",
            {
                "file_name": file_name,
                "file_base64": base64.b64encode(data).decode("ascii"),
                "upload_type": "file_upload",
                "model": model,
            },
            timeout=180,
        )
        record = unwrap(payload) or {}
        clip_id = first_string(record.get("clip_id") or record.get("song_id") or record.get("id"))
        if not clip_id:
            raise AppError(502, "UPLOAD_INVALID", "Suno API 未返回源音频 ID。", payload)
        return {"clip_id": clip_id, "raw": payload}

    def create_voice(self, file_name: str, data: bytes, title: str) -> dict[str, Any]:
        payload = self._request(
            "/api/music/voice-profile/create",
            "POST",
            {
                "file_name": file_name,
                "file_base64": base64.b64encode(data).decode("ascii"),
                "title": title,
                "source_kind": "upload",
                "client_request_id": f"voice-{uuid.uuid4().hex}",
            },
            timeout=180,
        )
        voice = normalize_voice(unwrap(payload))
        if not voice:
            raise AppError(502, "VOICE_INVALID", "音色创建响应中没有 voice_id。", payload)
        return voice

    def list_voices(self) -> list[dict[str, Any]]:
        payload = self._request("/api/music/voice-profile/list")
        data = unwrap(payload)
        items: list[Any] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("items", "voices", "list", "results"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        voices = [normalize_voice(item) for item in items]
        return [voice for voice in voices if voice]

    def source_cover(
        self,
        clip_id: str,
        style_tags: str,
        lyrics: str,
        title: str,
        retention: int,
        max_mode: bool,
        idempotency_key: str,
        model: str = MODEL,
    ) -> dict[str, Any]:
        if model not in MODELS:
            raise AppError(400, "MODEL_INVALID", "不支持的模型版本。")
        variety = max(0, min(4, round((100 - max(0, min(100, retention))) / 25)))
        body = compact({
            "model": model,
            "source_clip_id": clip_id,
            "lyrics": lyrics,
            "style_tags": style_tags,
            "song_title": title,
            "instrumental": False,
            "max_mode": max_mode,
            "variety": variety,
            "accept_mode": "async",
            "client_request_id": idempotency_key,
        })
        payload = self._request(
            "/api/music/generate-from-source",
            "POST",
            body,
            timeout=120,
            extra_headers={"Idempotency-Key": idempotency_key},
        )
        return normalize_submission(payload)


    def voice_cover(
        self,
        voice_id: str,
        clip_id: str,
        style_tags: str,
        lyrics: str,
        title: str,
        retention: int,
        max_mode: bool,
        idempotency_key: str,
        model: str = MODEL,
    ) -> dict[str, Any]:
        if model not in MODELS:
            raise AppError(400, "MODEL_INVALID", "不支持的模型版本。")
        body = compact({
            "model": model,
            "voice_id": voice_id,
            "song_clip_id": clip_id,
            "lyrics": lyrics,
            "style_requirements": style_tags,
            "audio_weight": round(max(0, min(100, retention)) / 100, 2),
            "max_mode": max_mode,
            "title": title,
        })
        payload = self._request(
            "/api/music/voice-profile/cover",
            "POST",
            body,
            timeout=120,
            extra_headers={"X-Create-Ui-Client-Request-Id": idempotency_key},
        )
        return normalize_submission(payload)

    def fresh_generate(
        self,
        style_tags: str,
        lyrics: str,
        title: str,
        max_mode: bool,
        idempotency_key: str,
        model: str = MODEL,
    ) -> dict[str, Any]:
        if model not in MODELS:
            raise AppError(400, "MODEL_INVALID", "不支持的模型版本。")
        body = compact({
            "prompt": lyrics,
            "tags": style_tags,
            "title": title,
            "model": model,
            "instrumental": False,
            "vocal_gender": "male",
            "style_weight": 0.85,
            "weirdness_constraint": 0.35,
            "max_mode": max_mode,
            "accept_mode": "async",
        })
        payload = self._request(
            "/api/music/create/custom",
            "POST",
            body,
            timeout=120,
            extra_headers={"Idempotency-Key": idempotency_key},
        )
        return normalize_submission(payload)

    def generate_lyrics(
        self,
        instruction: str,
        style: str = "",
        title: str = "",
        model: str = MODEL,
    ) -> dict[str, Any]:
        model = selected_model(model)
        body = compact({
            "instruction": instruction,
            "style": style,
            "title": title,
            "model": model,
        })
        payload = self._request("/api/lyrics/generate", "POST", body, timeout=90)
        text = extract_lyrics_text(unwrap(payload))
        if not text:
            raise AppError(502, "LYRICS_INVALID", "Suno API 没有返回可用歌词。", payload)
        return {"lyrics": text, "raw": payload}

    def get_request(self, request_id: str) -> dict[str, Any]:
        payload = self._request(f"/api/music/requests/{urllib.parse.quote(request_id, safe='')}")
        return normalize_submission(payload)

    def query_songs(self, song_ids: list[str]) -> list[dict[str, Any]]:
        if not song_ids:
            return []
        payload = self._request(
            "/api/music/query",
            "POST",
            {"model": MODEL, "song_ids": ",".join(song_ids)},
        )
        songs: list[dict[str, Any]] = []
        collect_songs(unwrap(payload), songs, set())
        return songs

    def list_songs(
        self,
        page: int = 1,
        page_size: int = 20,
        query: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        params = {"p": str(max(1, page)), "page_size": str(max(1, min(50, page_size)))}
        if query:
            params["q"] = query
        if status:
            params["status"] = status
        payload = self._request(f"/api/music/songs?{urllib.parse.urlencode(params)}")
        data = unwrap(payload) or {}
        songs: list[dict[str, Any]] = []
        seen: set[str] = set()
        if isinstance(data, dict):
            for key in ("items", "songs", "list", "results"):
                collect_songs(data.get(key), songs, seen)
        if not songs:
            collect_songs(data, songs, seen)
        record = data if isinstance(data, dict) else {}
        return {
            "songs": songs,
            "total": record.get("total", len(songs)),
            "page": record.get("page", page),
            "page_size": record.get("page_size", page_size),
        }

    def download_bytes(self, song_id: str, kind: str) -> dict[str, Any]:
        return self._request(
            "/api/music/download-file",
            "POST",
            {"song_id": song_id, "kind": kind},
            timeout=180,
            binary=True,
        )

    def download_url(self, song_id: str, kind: str = "song") -> str | None:
        payload = self._request(
            "/api/music/download-url",
            "POST",
            {"song_id": song_id, "kind": kind, "refresh": True},
        )
        return find_url(unwrap(payload))

    def prepare_wav(self, song_id: str) -> dict[str, Any]:
        return self._request("/api/music/wav", "POST", {"song_id": song_id, "model": MODEL})

    def separate(self, song_id: str, stem_mode: str, model: str = MODEL) -> dict[str, Any]:
        if stem_mode not in ("two", "twelve"):
            raise AppError(400, "STEM_MODE", "分轨模式必须是 two 或 twelve。")
        model = selected_model(model)
        payload = self._request(
            "/api/music/separate",
            "POST",
            {
                "model": model,
                "song_id": song_id,
                "stem_mode": stem_mode,
                "kind": "song",
                "wait_completion": False,
            },
            timeout=120,
        )
        return normalize_submission(payload)

    def vocal_removal(self, song_id: str, title: str, model: str = MODEL) -> dict[str, Any]:
        model = selected_model(model)
        payload = self._request(
            "/api/music/vocal-removal",
            "POST",
            {
                "model": model,
                "source_clip_id": song_id,
                "song_title": title,
                "wait_completion": False,
            },
            timeout=120,
        )
        return normalize_submission(payload)

    def edit_song(
        self,
        song_id: str,
        title: str,
        operation: str,
        params: dict[str, Any],
        model: str = MODEL,
    ) -> dict[str, Any]:
        if model not in MODELS:
            raise AppError(400, "MODEL_INVALID", "Unsupported model.")
        op = operation.strip().lower()
        start = params.get("start_seconds")
        end = params.get("end_seconds")
        if op in ("crop", "remove_section"):
            if (
                not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or float(start) < 0
                or float(end) <= float(start)
            ):
                raise AppError(400, "EDIT_PARAM", "Crop range is invalid.")
            path = "/api/music/crop" if op == "crop" else "/api/music/remove-section"
            body = {
                "source_clip_id": song_id,
                "crop_start_s": round(float(start), 2),
                "crop_end_s": round(float(end), 2),
                "song_title": title,
            }
        elif op == "fade":
            direction = str(params.get("direction") or "out").lower()
            duration = params.get("duration_seconds")
            if direction not in ("in", "out"):
                raise AppError(400, "EDIT_PARAM", "Fade direction must be in or out.")
            if not isinstance(duration, (int, float)) or float(duration) <= 0:
                raise AppError(400, "EDIT_PARAM", "Fade duration must be positive.")
            path = "/api/music/fade"
            body = {
                "source_clip_id": song_id,
                "direction": direction,
                "duration_seconds": round(float(duration), 2),
                "song_title": title,
            }
        elif op == "speed":
            multiplier = params.get("speed_multiplier")
            if (
                not isinstance(multiplier, (int, float))
                or not 0.25 <= float(multiplier) <= 4
            ):
                raise AppError(400, "EDIT_PARAM", "Speed multiplier must be between 0.25 and 4.")
            path = "/api/music/speed"
            body = {
                "source_clip_id": song_id,
                "speed_multiplier": round(float(multiplier), 3),
                "keep_pitch": bool(params.get("keep_pitch", True)),
                "song_title": title,
            }
        elif op == "reverse":
            path = "/api/music/reverse"
            body = {"source_clip_id": song_id, "song_title": title}
        else:
            raise AppError(400, "EDIT_OP_INVALID", "Unsupported edit operation.")
        payload = self._request(
            path,
            "POST",
            compact({"model": model, "wait_completion": False, **body}),
            timeout=120,
        )
        return normalize_submission(payload)


def probe_account(
    api: SunoApi,
    timeout: int = 20,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        balance_future = pool.submit(api.balance, timeout)
        prices_future = pool.submit(api.prices, timeout)
        return balance_future.result(), prices_future.result()


class Store:

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "uploads": [], "voices": [], "jobs": [], "downloads": []}

    def read(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.exists():
                return self._empty()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return self._empty()
            for key, value in self._empty().items():
                data.setdefault(key, value)
            return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        text = json.dumps(data, ensure_ascii=False, indent=2)
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, self.path)

    def update(self, mutator: Any) -> dict[str, Any]:
        with self.lock:
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    data = self._empty()
            else:
                data = self._empty()
            for key, value in self._empty().items():
                data.setdefault(key, value)
            mutator(data)
            self._write(data)
            return data

    def add_upload(self, item: dict[str, Any]) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["uploads"].insert(0, item)
            data["uploads"] = data["uploads"][:50]
        self.update(mutate)

    def set_voices(self, voices: list[dict[str, Any]]) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["voices"] = voices
        self.update(mutate)

    def add_job(self, job: dict[str, Any]) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["jobs"] = [item for item in data["jobs"] if item.get("id") != job.get("id")]
            data["jobs"].insert(0, job)
            data["jobs"] = data["jobs"][:100]
        self.update(mutate)

    def update_job(self, result: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        box: dict[str, Any] = {}

        def mutate(data: dict[str, Any]) -> None:
            target = None
            for job in data["jobs"]:
                if request_id and job.get("request_id") == request_id:
                    target = job
                    break
                if result.get("id") and job.get("id") == result.get("id"):
                    target = job
                    break
            if target is None:
                target = {"id": result.get("id") or f"job-{uuid.uuid4().hex[:12]}"}
                data["jobs"].insert(0, target)
            target.update(result)
            target["updated_at"] = utc_now()
            data["jobs"] = data["jobs"][:100]
            box["job"] = dict(target)

        self.update(mutate)
        return box.get("job") or result

    def find_job(self, job_id: str | None = None, request_id: str | None = None) -> dict[str, Any] | None:
        data = self.read()
        for job in data.get("jobs", []):
            if job_id and job.get("id") == job_id:
                return job
            if request_id and job.get("request_id") == request_id:
                return job
        return None

    def add_download(self, item: dict[str, Any]) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["downloads"].insert(0, item)
            data["downloads"] = data["downloads"][:100]
        self.update(mutate)

    def remove_download(self, filename: str) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["downloads"] = [
                item for item in data["downloads"] if item.get("filename") != filename
            ]
        self.update(mutate)


STORE = Store(STATE_PATH)


def new_job(kind: str, mode: str, title: str, amount: float) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": f"job-{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "mode": mode,
        "title": title,
        "estimate": amount,
        "status": "submitting",
        "request_id": None,
        "songs": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
    }


def merge_songs(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for song in primary + secondary:
        song_id = song.get("song_id")
        if not song_id:
            continue
        if song_id not in merged:
            merged[song_id] = song
            order.append(song_id)
        else:
            merged[song_id].update({key: value for key, value in song.items() if value})
    return [merged[song_id] for song_id in order]


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    clean = dict(job)
    clean.pop("idempotency_key", None)
    return clean

def require_confirmed_cost(estimate: dict[str, Any], expected: Any, confirm: Any) -> None:
    if confirm is not True:
        raise AppError(403, "CONFIRMATION_REQUIRED", "付费操作需要明确确认。")
    try:
        expected_amount = round(float(expected), 2)
    except (TypeError, ValueError) as exc:
        raise AppError(400, "ESTIMATE_REQUIRED", "缺少确认后的价格。") from exc
    if abs(expected_amount - float(estimate["amount"])) > 0.001:
        raise AppError(409, "PRICE_CHANGED", "价格已变化，请重新估价并确认。")


def download_remote(url: str, timeout: int = 180) -> tuple[bytes, str]:
    if not url.startswith(("http://", "https://")):
        raise AppError(400, "URL_INVALID", "下载地址无效。")
    request = urllib.request.Request(url, headers={"User-Agent": "SifengMusicStudio/0.1"})
    try:
        with create_url_opener().open(request, timeout=timeout) as response:
            return response.read(), response.headers.get("content-type", "application/octet-stream")
    except Exception as exc:
        raise AppError(502, "DOWNLOAD_FAILED", f"媒体下载失败：{exc}") from exc


def save_song_file(api: SunoApi, song_id: str, fmt: str, title: str) -> dict[str, Any]:
    fmt = fmt.lower()
    if fmt not in ("mp3", "wav"):
        raise AppError(400, "FORMAT_INVALID", "只支持导出 MP3 或 WAV。")
    content_type = ""
    data: bytes | None = None
    if fmt == "wav":
        try:
            prepared = api.prepare_wav(song_id)
            prepared_url = find_url(unwrap(prepared))
            if prepared_url:
                data, content_type = download_remote(prepared_url)
        except AppError:
            data = None
        if data is None:
            try:
                result = api.download_bytes(song_id, "wav")
                data = result["bytes"]
                content_type = result["content_type"]
            except AppError:
                try:
                    url = api.download_url(song_id, "wav")
                except AppError:
                    url = None
                if not url:
                    raise AppError(404, "MEDIA_NOT_FOUND", "WAV 文件尚未准备好。")
                data, content_type = download_remote(url)
    else:
        try:
            result = api.download_bytes(song_id, "mp3")
            data = result["bytes"]
            content_type = result["content_type"]
        except AppError:
            url = None
            for kind in ("mp3", "song"):
                try:
                    url = api.download_url(song_id, kind)
                except AppError:
                    url = None
                if url:
                    break
            if not url:
                raise AppError(404, "MEDIA_NOT_FOUND", "MP3 文件尚未准备好。")
            data, content_type = download_remote(url)
    if not data or len(data) < 1000:
        raise AppError(502, "MEDIA_EMPTY", "下载到的媒体文件为空。")
    if fmt == "wav" and not data.startswith(b"RIFF"):
        raise AppError(502, "MEDIA_INVALID", "下载内容不是有效的 WAV 文件。")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(title, "song")[:45]
    filename = f"{stem}_{song_id[:10]}_{int(time.time())}.{fmt}"
    target = OUTPUT_DIR / filename
    target.write_bytes(data)
    item = {
        "song_id": song_id,
        "format": fmt,
        "title": title,
        "filename": filename,
        "size": len(data),
        "content_type": content_type,
        "created_at": utc_now(),
    }
    STORE.add_download(item)
    return {**item, "url": f"/outputs/{urllib.parse.quote(filename)}"}


def list_output_files(limit: int = 100) -> list[dict[str, Any]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records = {
        item.get("filename"): item
        for item in STORE.read().get("downloads", [])
        if item.get("filename")
    }
    files: list[dict[str, Any]] = []
    for path in sorted(OUTPUT_DIR.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file() or path.suffix.lower() not in OUTPUT_EXTENSIONS:
            continue
        record = records.get(path.name) or {}
        stat = path.stat()
        files.append({
            "filename": path.name,
            "url": f"/outputs/{urllib.parse.quote(path.name)}",
            "format": path.suffix.lower().lstrip("."),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds"),
            "title": record.get("title") or Path(path.name).stem,
            "song_id": record.get("song_id"),
        })
        if len(files) >= max(1, min(500, limit)):
            break
    return files


def delete_output_file(filename: str) -> dict[str, Any]:
    raw = str(filename or "").strip()
    name = Path(raw).name
    if not name or name != raw or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise AppError(400, "OUTPUT_NAME_INVALID", "输出文件名无效。")
    if Path(name).suffix.lower() not in OUTPUT_EXTENSIONS:
        raise AppError(400, "OUTPUT_TYPE_INVALID", "只能删除本地 MP3 或 WAV 文件。")
    base = OUTPUT_DIR.resolve()
    target = (OUTPUT_DIR / name).resolve()
    if target.parent != base:
        raise AppError(403, "PATH_DENIED", "禁止访问该路径。")
    if not target.is_file():
        raise AppError(404, "OUTPUT_NOT_FOUND", "本地输出文件不存在。")
    target.unlink()
    STORE.remove_download(name)
    return {"filename": name, "deleted": True}


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "SifengStudio/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    def _send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, download: bool = False) -> None:
        if not path.is_file():
            self._send_json(404, {"ok": False, "error": "文件不存在。"})
            return
        data = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        if download:
            quoted = urllib.parse.quote(path.name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise AppError(400, "BODY_INVALID", "请求长度无效。") from exc
        if length <= 0:
            return {}
        if length > MAX_REQUEST_BYTES:
            raise AppError(413, "BODY_TOO_LARGE", "上传内容过大。")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise AppError(400, "JSON_INVALID", "请求 JSON 无效。") from exc
        if not isinstance(value, dict):
            raise AppError(400, "JSON_INVALID", "请求必须是 JSON 对象。")
        return value

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, UnicodeEncodeError):
            self._send_json(400, {
                "ok": False,
                "code": "REQUEST_ENCODING",
                "error": "请求包含中文或无法编码的字符，请检查 API Key、Base URL 或输入内容。",
            })
            return
        if isinstance(exc, AppError):
            payload = {"ok": False, "code": exc.code, "error": redact(exc.message)}
            if exc.details is not None:
                payload["details"] = exc.details
            self._send_json(exc.status, payload)
            return
        print(f"[error] {type(exc).__name__}: {redact(str(exc))}")
        self._send_json(500, {"ok": False, "code": "INTERNAL", "error": "程序内部错误。"})

    def do_GET(self) -> None:
        try:
            self._get()
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self) -> None:
        try:
            self._post()
        except Exception as exc:
            self._handle_error(exc)

    def _get(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path in ("/", "/simple"):
            self._send_file(WEB_DIR / "simple.html")
            return
        static_files = {
            "/simple.css": "simple.css",
            "/simple.js": "simple.js",
        }
        if path in static_files:
            self._send_file(WEB_DIR / static_files[path])
            return
        if path.startswith("/outputs/"):
            name = urllib.parse.unquote(path[len("/outputs/"):])
            target = (OUTPUT_DIR / safe_filename(name)).resolve()
            base = OUTPUT_DIR.resolve()
            if base not in target.parents:
                raise AppError(403, "PATH_DENIED", "禁止访问该路径。")
            self._send_file(target)
            return
        if path == "/api/health":
            self._send_json(200, {"ok": True, "service": "sifeng-music-studio"})
            return
        if path == "/api/config":
            self._send_json(200, {
                "ok": True,
                "config": config_summary(),
                "defaults": {
                    "style": SIFENG_STYLE,
                    "title": "司凤新作",
                    "model": "suno-v6-wild",
                },
                "models": list(MODEL_INFO),
            })
            return
        if path == "/api/status":
            result: dict[str, Any] = {
                "ok": True,
                "config_found": CONFIG_PATH.exists(),
                "config_path": str(CONFIG_PATH),
                "output_dir": str(OUTPUT_DIR),
                "model": MODEL,
                "models": list(MODEL_INFO),
                "config": config_summary(),
            }
            try:
                api = SunoApi()
                balance, pricing = probe_account(api, 20)
                result["balance"] = balance
                result["prices"] = pricing["prices"]
                result["promotion_active"] = pricing["promotion_active"]
            except AppError as exc:
                result.update({"ok": False, "error": exc.message, "code": exc.code})
            self._send_json(200, result)
            return
        if path == "/api/voices":
            api = SunoApi()
            voices = api.list_voices()
            STORE.set_voices(voices)
            self._send_json(200, {"ok": True, "voices": voices})
            return
        if path == "/api/songs":
            try:
                page = int((query.get("page") or ["1"])[0])
                page_size = int((query.get("page_size") or ["20"])[0])
            except ValueError as exc:
                raise AppError(400, "PAGE_INVALID", "???????") from exc
            api = SunoApi()
            result = api.list_songs(
                page=page,
                page_size=page_size,
                query=str((query.get("q") or [""])[0]).strip(),
                status=str((query.get("status") or [""])[0]).strip(),
            )
            self._send_json(200, {"ok": True, **result})
            return
        if path == "/api/history":
            data = STORE.read()
            jobs = [public_job(item) for item in data.get("jobs", [])]
            self._send_json(200, {
                "ok": True,
                "jobs": jobs,
                "uploads": data.get("uploads", [])[:20],
                "downloads": data.get("downloads", [])[:20],
            })
            return
        if path == "/api/outputs":
            self._send_json(200, {
                "ok": True,
                "output_dir": str(OUTPUT_DIR),
                "files": list_output_files(),
            })
            return
        if path == "/api/job":
            self._get_job(query)
            return
        raise AppError(404, "NOT_FOUND", "接口不存在。")

    def _get_job(self, query: dict[str, list[str]]) -> None:
        job_id = (query.get("job_id") or [None])[0]
        request_id = (query.get("request_id") or [None])[0]
        job = STORE.find_job(job_id=job_id, request_id=request_id)
        if not request_id and job:
            request_id = job.get("request_id")
        if not request_id:
            if job:
                self._send_json(200, {"ok": True, "job": public_job(job)})
                return
            raise AppError(400, "JOB_REQUIRED", "缺少任务 ID。")
        api = SunoApi()
        result = api.get_request(request_id)
        songs = result.get("songs") or []
        if result.get("status") in TERMINAL_STATUSES:
            known = {item.get("song_id") for item in songs}
            missing = [item["song_id"] for item in songs if not item.get("audio_url")]
            task_ids = [task.get("song_id") for task in result.get("tasks", []) if task.get("song_id")]
            query_ids = list(dict.fromkeys(missing + [item for item in task_ids if item not in known]))
            if query_ids:
                songs = merge_songs(songs, api.query_songs(query_ids))
        task_error = next((task.get("error_message") for task in result.get("tasks", [])
                           if task.get("error_message")), None)
        base = dict(job or {"id": f"job-{uuid.uuid4().hex[:12]}", "kind": "generation"})
        base.update({
            "request_id": request_id,
            "status": result.get("status"),
            "songs": songs,
            "error": task_error,
            "updated_at": utc_now(),
        })
        saved = STORE.update_job(base, request_id=request_id)
        self._send_json(200, {"ok": True, "job": public_job(saved), "submission": result})

    def _post(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json()
        if self.headers.get("X-Sifeng-Client") != "1":
            raise AppError(403, "CLIENT_HEADER_REQUIRED", "Missing local client header.")
        if path == "/api/estimate":
            mode = str(body.get("mode") or "source")
            api = SunoApi()
            estimate = api.estimate(mode, bool(body.get("max_mode")))
            self._send_json(200, {"ok": True, "estimate": estimate})
            return
        if path == "/api/config":
            self._save_config(body)
            return
        if path == "/api/config/test":
            self._test_config()
            return
        if path == "/api/lyrics/refine":
            result = refine_sifeng_lyrics(
                str(body.get("lyrics") or ""),
                str(body.get("title") or ""),
            )
            self._send_json(200, {"ok": True, **result})
            return
        if path == "/api/lyrics/draft":
            idea = str(body.get("idea") or "").strip()
            lyrics = draft_sifeng_lyrics(idea)
            self._send_json(200, {
                "ok": True,
                "lyrics": lyrics,
                "title": "悟了，但没完全悟",
            })
            return
        if path == "/api/lyrics/generate":
            self._ai_lyrics(body)
            return
        if path == "/api/upload":
            self._upload_source(body)
            return
        if path == "/api/voice/create":
            self._create_voice(body)
            return
        if path == "/api/generate":
            self._generate(body)
            return
        if path == "/api/stems":
            self._stems(body)
            return
        if path == "/api/edit":
            self._edit(body)
            return
        if path == "/api/download":
            self._download(body)
            return
        if path == "/api/outputs/delete":
            self._send_json(200, {"ok": True, **delete_output_file(str(body.get("filename") or ""))})
            return
        if path == "/api/open-folder":
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(OUTPUT_DIR))
            else:
                raise AppError(501, "UNSUPPORTED", "当前系统不支持自动打开文件夹。")
            self._send_json(200, {"ok": True, "path": str(OUTPUT_DIR)})
            return
        raise AppError(404, "NOT_FOUND", "接口不存在。")

    def _save_config(self, body: dict[str, Any]) -> None:
        raw_key = str(body.get("api_key") or "").strip()
        raw_url = str(body.get("base_url") or "").strip()
        if raw_key:
            key = normalize_api_key(raw_key)
        else:
            try:
                key, _ = load_config()
            except AppError as exc:
                if exc.code == "CONFIG_MISSING":
                    raise AppError(400, "API_KEY_REQUIRED", "首次配置请填写 API Key。") from exc
                raise
        if raw_url:
            base_url = normalize_base_url(raw_url)
        else:
            try:
                _, base_url = load_config()
            except AppError:
                base_url = DEFAULT_BASE_URL
        probe = object.__new__(SunoApi)
        probe.api_key = key
        probe.base_url = base_url
        balance, pricing = probe_account(probe, 20)
        save_config(key, base_url)
        self._send_json(200, {
            "ok": True,
            "config": config_summary(),
            "balance": balance,
            "prices": pricing["prices"],
            "promotion_active": pricing["promotion_active"],
        })

    def _test_config(self) -> None:
        api = SunoApi()
        balance, pricing = probe_account(api, 20)
        self._send_json(200, {
            "ok": True,
            "config": config_summary(),
            "balance": balance,
            "prices": pricing["prices"],
            "promotion_active": pricing["promotion_active"],
        })

    def _ai_lyrics(self, body: dict[str, Any]) -> None:
        if body.get("confirm_external") is not True:
            raise AppError(
                403,
                "EXTERNAL_CONFIRMATION_REQUIRED",
                "AI 写词会把你的想法发送到 suno-api.io，请先确认。",
            )
        if body.get("confirm_cost") is not True:
            raise AppError(
                403,
                "CONFIRMATION_REQUIRED",
                "AI 写词超出免费配额时可能产生少量费用，请先确认。",
            )
        instruction = str(body.get("instruction") or body.get("idea") or "").strip()
        if not instruction:
            raise AppError(400, "IDEA_REQUIRED", "请先写一句想法或写词要求。")
        require_text_limit("instruction", instruction, 2000, "写词要求")
        style = str(body.get("style") or SIFENG_STYLE).strip()
        title = str(body.get("title") or "司凤新作").strip()[:100]
        model = selected_model(body.get("model") or "suno-v6-wild")
        api = SunoApi()
        result = api.generate_lyrics(instruction, style, title, model)
        lyrics = result["lyrics"]
        notes: list[str] = []
        if body.get("refine", True) is not False:
            refined = refine_sifeng_lyrics(lyrics, title)
            lyrics = refined["lyrics"]
            notes = refined["notes"]
        self._send_json(200, {
            "ok": True,
            "source": "suno",
            "lyrics": lyrics,
            "title": title,
            "notes": notes,
        })

    def _upload_source(self, body: dict[str, Any]) -> None:
        if body.get("confirm_upload") is not True:
            raise AppError(403, "UPLOAD_CONFIRMATION_REQUIRED", "上传音频需要明确确认。")
        name, data = decode_audio(str(body.get("file_name") or "song.mp3"), str(body.get("file_base64") or ""))
        api = SunoApi()
        result = api.upload_source(name, data, selected_model(body.get("model")))
        item = {
            "clip_id": result["clip_id"],
            "file_name": name,
            "size": len(data),
            "created_at": utc_now(),
        }
        STORE.add_upload(item)
        self._send_json(200, {"ok": True, **item})

    def _create_voice(self, body: dict[str, Any]) -> None:
        if body.get("confirm_upload") is not True:
            raise AppError(403, "UPLOAD_CONFIRMATION_REQUIRED", "上传音色样本需要明确确认。")
        name, data = decode_audio(
            str(body.get("file_name") or "voice.mp3"),
            str(body.get("file_base64") or ""),
            voice=True,
        )
        title = str(body.get("title") or "司凤音色").strip()[:60] or "司凤音色"
        api = SunoApi()
        voice = api.create_voice(name, data, title)
        voices = STORE.read().get("voices", [])
        voices = [item for item in voices if item.get("voice_id") != voice.get("voice_id")]
        voices.insert(0, voice)
        STORE.set_voices(voices)
        self._send_json(200, {"ok": True, "voice": voice, "file_name": name, "size": len(data)})

    def _generate(self, body: dict[str, Any]) -> None:
        mode = str(body.get("mode") or "source")
        if mode not in ("source", "voice", "fresh"):
            raise AppError(400, "MODE_INVALID", "生成模式无效。")
        title = str(body.get("title") or "茉莉花 · 司凤电子改编").strip()[:100]
        style_tags = str(body.get("style_tags") or "").strip()
        lyrics = str(body.get("lyrics") or "").strip()
        if not style_tags:
            raise AppError(400, "STYLE_REQUIRED", "请填写或选择音乐风格。")
        if mode != "fresh" and not lyrics:
            raise AppError(400, "LYRICS_REQUIRED", "旋律改编需要提供歌词，避免误听。")
        require_text_limit("style_tags", style_tags, 1000, "风格提示词")
        require_text_limit("lyrics", lyrics, 5000, "歌词")
        try:
            retention = max(0, min(100, int(body.get("retention", 85))))
        except (TypeError, ValueError) as exc:
            raise AppError(400, "RETENTION_INVALID", "旋律保留程度必须是 0-100。") from exc
        max_mode = bool(body.get("max_mode"))
        model = selected_model(body.get("model"))
        clip_id = str(body.get("clip_id") or "").strip()
        voice_id = str(body.get("voice_id") or "").strip()
        if mode in ("source", "voice") and not clip_id:
            raise AppError(400, "SOURCE_REQUIRED", "请先选择并上传源音频。")
        if mode == "voice" and not voice_id:
            raise AppError(400, "VOICE_REQUIRED", "请先选择可用的司凤音色。")
        api = SunoApi()
        estimate = api.estimate(mode, max_mode)
        require_confirmed_cost(estimate, body.get("expected_amount"), body.get("confirm_cost"))
        idempotency_key = str(body.get("idempotency_key") or uuid.uuid4().hex)
        job = new_job("generation", mode, title, float(estimate["amount"]))
        job["idempotency_key"] = idempotency_key
        job["model"] = model
        STORE.add_job(job)
        try:
            if mode == "voice":
                result = api.voice_cover(voice_id, clip_id, style_tags, lyrics, title,
                                         retention, max_mode, idempotency_key, model)
            elif mode == "source":
                result = api.source_cover(clip_id, style_tags, lyrics, title,
                                          retention, max_mode, idempotency_key, model)
            else:
                result = api.fresh_generate(style_tags, lyrics, title, max_mode,
                                            idempotency_key, model)
        except AppError as exc:
            job["status"] = "unknown" if exc.code in ("TIMEOUT", "NETWORK_ERROR") else "failed"
            job["error"] = exc.message
            STORE.add_job(job)
            raise
        job.update({
            "status": result.get("status") or "queued",
            "request_id": result.get("request_id"),
            "songs": result.get("songs") or [],
            "error": None,
            "updated_at": utc_now(),
        })
        STORE.add_job(job)
        self._send_json(200, {"ok": True, "job": public_job(job), "submission": result})

    def _stems(self, body: dict[str, Any]) -> None:
        mode = str(body.get("mode") or "stems_two")
        if mode not in ("stems_two", "stems_twelve", "vocal_removal"):
            raise AppError(400, "MODE_INVALID", "分轨模式无效。")
        song_id = str(body.get("song_id") or "").strip()
        if not song_id:
            raise AppError(400, "SONG_REQUIRED", "缺少歌曲 ID。")
        title = str(body.get("title") or "分轨任务").strip()[:100]
        model = selected_model(body.get("model"))
        api = SunoApi()
        estimate = api.estimate(mode, False)
        require_confirmed_cost(estimate, body.get("expected_amount"), body.get("confirm_cost"))
        job = new_job("stems", mode, title, float(estimate["amount"]))
        job["model"] = model
        STORE.add_job(job)
        try:
            if mode == "vocal_removal":
                result = api.vocal_removal(song_id, title, model)
            else:
                result = api.separate(
                    song_id,
                    "twelve" if mode == "stems_twelve" else "two",
                    model,
                )
        except AppError as exc:
            job["status"] = "unknown" if exc.code in ("TIMEOUT", "NETWORK_ERROR") else "failed"
            job["error"] = exc.message
            STORE.add_job(job)
            raise
        job.update({
            "status": result.get("status") or "queued",
            "request_id": result.get("request_id"),
            "songs": result.get("songs") or [],
            "error": None,
            "updated_at": utc_now(),
        })
        STORE.add_job(job)
        self._send_json(200, {"ok": True, "job": public_job(job), "submission": result})

    def _edit(self, body: dict[str, Any]) -> None:
        song_id = str(body.get("song_id") or "").strip()
        if not song_id:
            raise AppError(400, "SONG_REQUIRED", "Missing song ID.")
        operation = str(body.get("operation") or "").strip().lower()
        if operation not in ("crop", "remove_section", "fade", "speed", "reverse"):
            raise AppError(400, "EDIT_OP_INVALID", "Unsupported edit operation.")
        title = str(body.get("title") or "Edit").strip()[:100]
        model = selected_model(body.get("model"))
        params = {
            key: body.get(key)
            for key in (
                "start_seconds",
                "end_seconds",
                "direction",
                "duration_seconds",
                "speed_multiplier",
                "keep_pitch",
            )
        }
        api = SunoApi()
        estimate = api.estimate("edit", False)
        require_confirmed_cost(estimate, body.get("expected_amount"), body.get("confirm_cost"))
        job = new_job("edit", operation, title, float(estimate["amount"]))
        job["model"] = model
        STORE.add_job(job)
        try:
            result = api.edit_song(song_id, title, operation, params, model)
        except AppError as exc:
            job["status"] = "unknown" if exc.code in ("TIMEOUT", "NETWORK_ERROR") else "failed"
            job["error"] = exc.message
            STORE.add_job(job)
            raise
        job.update({
            "status": result.get("status") or "queued",
            "request_id": result.get("request_id"),
            "songs": result.get("songs") or [],
            "error": None,
            "updated_at": utc_now(),
        })
        STORE.add_job(job)
        self._send_json(200, {"ok": True, "job": public_job(job), "submission": result})

    def _download(self, body: dict[str, Any]) -> None:
        song_id = str(body.get("song_id") or "").strip()
        if not song_id:
            raise AppError(400, "SONG_REQUIRED", "缺少歌曲 ID。")
        title = str(body.get("title") or "茉莉花改编").strip()
        fmt = str(body.get("format") or "mp3").lower()
        api = SunoApi()
        item = save_song_file(api, song_id, fmt, title)
        self._send_json(200, {"ok": True, "file": item})


def make_server(port: int) -> ThreadingHTTPServer:
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), StudioHandler)
            return server
        except OSError:
            continue
    raise RuntimeError("没有可用端口，请关闭占用 8765-8774 端口的程序。")


def main() -> None:
    parser = argparse.ArgumentParser(description="司凤音造局本地音乐改编工作台")
    parser.add_argument("--port", type=int, default=8765, help="本地端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="不要自动打开浏览器")
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = make_server(args.port)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print("=" * 58)
    print("司凤音造局已启动")
    print(f"界面地址：{url}")
    print(f"输出目录：{OUTPUT_DIR}")
    print("按 Ctrl+C 停止服务。关闭此窗口也会停止程序。")
    print("=" * 58)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
