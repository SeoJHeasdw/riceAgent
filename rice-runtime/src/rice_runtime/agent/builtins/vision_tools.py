"""Builtin vision tool: analyze an image via the current LLM provider.

Adapted from Hermes copy/tools/vision_tools.py.
Hermes-specific imports (agent.auxiliary_client, hermes_constants, tools.website_policy,
tools.debug_helpers) have been removed and replaced with RICE-native stdlib + httpx.

KNOWN LIMITATION — MULTIMODAL NOT YET WIRED INTO ILLMProvider:
  The current `ILLMProvider.stream()` accepts `messages: Sequence[Message]` where
  `Message.content` is `str`. There is NO standard mechanism in the RICE provider
  interface to pass image data (base64 or URL) as a structured multimodal content
  block to the model.

  `ClaudeProvider.capabilities()` already returns `vision=True`, and the wire format
  for Claude vision (image content blocks) is known — but composing them from a
  builtin tool result string is a workaround, not a real multimodal path.

  WHAT THIS TOOL DOES INSTEAD:
    1. Downloads the image and converts it to a base64 data-URL string.
    2. Returns that data-URL as the tool result text.
    3. The LLM provider receives the base64 string as a tool result in the
       conversation history, which works for Claude (it reads base64 inline)
       but may silently produce poor results for non-vision providers.

  TODO: widen ILLMProvider / Message to carry structured image content blocks
  so vision is handled cleanly at the provider seam.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

from ...core.paths import RicePaths
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool

# httpx is already a dependency of rice-runtime (anthropic SDK pulls it in).
try:
    import httpx as _httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

_VISION_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024   # 50 MB hard cap
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


def _make_vision_analyze(workspace: Path) -> BuiltinTool:
    async def vision_analyze(args: dict) -> object:
        """
        ⚠️  MULTIMODAL WORKAROUND — see module docstring.
        Returns a base64 data-URL string as the tool result. The LLM provider
        receives this as a text string in the tool result history. Claude reads
        base64 inline; other providers may ignore it or produce degraded output.
        """
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
        else:
            # Treat as a path relative to workspace.
            abs_path = image_source if os.path.isabs(image_source) else str(workspace / image_source)
            b64, mime = _local_image_to_base64(abs_path)

        data_url = f"data:{mime};base64,{b64}"

        # Return the structured result that Claude can consume as an image.
        # NOTE: This relies on the LLM provider being Claude (or a compatible
        # model that handles base64 inline) and on the provider adapter being
        # extended in future to forward image content blocks properly.
        return {
            "question": question,
            "image_data_url": data_url,
            "mime_type": mime,
            "size_bytes": len(base64.b64decode(b64)),
            "_warning": (
                "This result contains a base64 image. Vision analysis requires "
                "a provider with vision=True capability (e.g. Claude). "
                "The model should describe the image in response to the question."
            ),
        }

    return vision_analyze


def tools(paths: RicePaths) -> list[tuple[ToolSpec, BuiltinTool]]:
    workspace = paths.workspace
    workspace.mkdir(parents=True, exist_ok=True)
    workspace = workspace.resolve()
    return [
        (
            ToolSpec(
                name="vision_analyze",
                description=(
                    "Download or read an image and return its base64-encoded data for visual analysis. "
                    "Works with image URLs (http/https) or workspace-relative file paths. "
                    "Supported formats: JPEG, PNG, GIF, WebP. "
                    "IMPORTANT: Requires a vision-capable provider (e.g. Claude). "
                    "After calling this tool, analyze the returned image_data_url to answer the question."
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
            _make_vision_analyze(workspace),
        ),
    ]
