"""Builtin vision tool: attach an image to the conversation for visual analysis.

Adapted from Hermes copy/tools/vision_tools.py.
Hermes-specific imports (agent.auxiliary_client, hermes_constants, tools.website_policy,
tools.debug_helpers) have been removed and replaced with RICE-native stdlib + httpx.

MULTIMODAL PATH (2026-07-10 — the old KNOWN LIMITATION is resolved):
  이 도구는 이미지를 base64 텍스트로 본문에 싣지 않는다. 결과 dict의 `_images`
  (core.types 이미지 첨부 계약)에 실으면:
    1. 디스패처가 `_images`를 분리해 `ToolResult.images`로 나른다
       (SSE payload에 base64가 새지 않는다).
    2. 루프가 tool Message의 `metadata["images"]`에 부착한다
       (transcript metadata JSON에 그대로 영속 — 스키마 변경 없음).
    3. provider 어댑터가 각자의 wire 이미지 블록으로 렌더한다:
       Claude tool_result image block / Ollama message.images(qwen-vl·llava) /
       OpenAI-compat image_url 파트(tool 블록 직후 합성 user 메시지).
  비전 없는 모델은 첨부를 무시한다(텍스트 요약만 보게 된다) — 결과의 note가
  그 경우를 모델에게 알린다.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

from ...core.paths import RicePaths
from ...core.types import TOOL_RESULT_IMAGES_KEY, ToolSpec
from ..tool_dispatch import BuiltinTool

# httpx is already a dependency of rice-runtime (anthropic SDK pulls it in).
try:
    import httpx as _httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

# 5 MB — Claude API의 이미지당 상한(가장 엄격한 wire)에 맞춘다. 이보다 큰
# 이미지는 어차피 provider에서 400으로 죽으므로 여기서 명확한 에러로 끊는 편이
# 낫다(구 50MB 상한은 base64 텍스트 시절의 잔재).
_VISION_MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
_VISION_DOWNLOAD_TIMEOUT = float(os.getenv("RICE_VISION_DOWNLOAD_TIMEOUT", "30"))

_SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
})


def _guess_mime(url: str, data: bytes) -> str:
    """Best-effort MIME type guess from URL extension or magic bytes."""
    # Magic bytes
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"

    # URL extension fallback
    ext = Path(urlparse(url).path).suffix.lower()
    mime, _ = mimetypes.guess_type(f"f{ext}")
    return mime or "image/jpeg"


async def _download_image(url: str) -> tuple[bytes, str]:
    """Download an image from `url`. Returns (raw_bytes, mime_type).
    Raises ValueError on failure.
    """
    if not _HTTPX_AVAILABLE:
        raise ValueError(
            "httpx is not installed. Install it with: pip install httpx"
        )

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are supported, got: {url!r}")

    async with _httpx.AsyncClient(timeout=_VISION_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise ValueError(
                    f"Failed to download image: HTTP {response.status_code} from {url!r}"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > _VISION_MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"Image exceeds size limit ({_VISION_MAX_DOWNLOAD_BYTES // (1024*1024)} MB): {url!r}"
                    )
                chunks.append(chunk)

    data = b"".join(chunks)
    mime = _guess_mime(url, data)
    return data, mime


def _local_image_to_base64(path_str: str) -> tuple[str, str]:
    """Read a local image file and return (base64_string, mime_type)."""
    path = Path(path_str)
    if not path.is_file():
        raise FileNotFoundError(f"Image file not found: {path_str!r}")
    data = path.read_bytes()
    if len(data) > _VISION_MAX_DOWNLOAD_BYTES:
        raise ValueError(
            f"Image exceeds size limit ({_VISION_MAX_DOWNLOAD_BYTES // (1024*1024)} MB): {path_str!r}"
        )
    mime = _guess_mime(path_str, data)
    return base64.standard_b64encode(data).decode("ascii"), mime


def _make_vision_analyze(paths: RicePaths) -> BuiltinTool:
    async def vision_analyze(args: dict) -> object:
        """이미지를 이 도구 결과에 **첨부**한다(모듈 docstring의 멀티모달 경로).
        본문에는 요약 메타데이터만 남고, 이미지는 provider의 이미지 블록으로
        모델에게 전달된다."""
        workspace = paths.workspace  # live: relocation applied at the turn boundary
        image_source: str = args.get("image_url") or args.get("image_path", "")
        question: str = args.get("question", "Describe this image in detail.")

        if not image_source:
            raise ValueError("Either image_url or image_path is required")

        # Determine if this is a URL or a local path.
        parsed = urlparse(image_source)
        is_url = parsed.scheme in ("http", "https")

        if is_url:
            raw_bytes, mime = await _download_image(image_source)
            b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
            size = len(raw_bytes)
        else:
            # Treat as a path relative to workspace.
            abs_path = image_source if os.path.isabs(image_source) else str(workspace / image_source)
            b64, mime = _local_image_to_base64(abs_path)
            size = len(base64.b64decode(b64))

        return {
            "question": question,
            "mime_type": mime,
            "size_bytes": size,
            "note": (
                "The image is attached to this tool result. Look at it and answer "
                "the question. If you cannot see any image, say so — your current "
                "model may not support vision."
            ),
            # core.types 이미지 첨부 계약 — 디스패처가 분리해 provider 이미지
            # 블록으로 나른다. base64를 본문/SSE에 싣지 않는 것이 핵심.
            TOOL_RESULT_IMAGES_KEY: [{"mime_type": mime, "data_b64": b64}],
        }

    return vision_analyze


def tools(paths: RicePaths) -> list[tuple[ToolSpec, BuiltinTool]]:
    paths.workspace.mkdir(parents=True, exist_ok=True)  # ensure initial root; handler reads live
    return [
        (
            ToolSpec(
                name="vision_analyze",
                description=(
                    "Load an image (http/https URL or workspace-relative path) and attach it to the "
                    "conversation so you can SEE it and answer questions about it. "
                    "Supported formats: JPEG, PNG, GIF, WebP (max 5 MB). "
                    "Requires a vision-capable model (Claude, qwen-vl, llava, gpt-4o, ...). "
                    "After calling this tool, look at the attached image and answer the question."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "image_url": {
                            "type": "string",
                            "description": "HTTP or HTTPS URL of the image to analyze.",
                        },
                        "image_path": {
                            "type": "string",
                            "description": "Workspace-relative path to a local image file.",
                        },
                        "question": {
                            "type": "string",
                            "description": "Question or instruction for the image analysis (default: describe the image).",
                        },
                    },
                    "required": [],
                },
                source="builtin",
            ),
            _make_vision_analyze(paths),
        ),
    ]
