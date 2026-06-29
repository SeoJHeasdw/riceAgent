"""rice-platform FastAPI application (RICE_PLATFORM_DESIGN.md, RICE_CONTRACTS.md §3).

엔드포인트:
    GET  /health                        서버 상태
    GET  /catalog                       artifact 목록 (type/q/category/tags/sort)
    GET  /artifacts/{id}                단일 artifact 상세
    GET  /artifacts/{id}/download       tar.gz 다운로드 (+ X-Checksum 헤더)
    POST /artifacts/{id}/sponsor        Blue Point 후원 (mock)
    POST /artifacts                     artifact 게시 (publish, MVP: 디렉토리 저장)

CORS: rice-app(Electron renderer, localhost)에서 접근하므로 localhost 전체 허용.
인증: 데모 단계 없음 (RICE_REPO_STRATEGY.md §4.2).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .registry import (
    build_download_package,
    get_artifact,
    list_artifacts,
    _artifacts_dir,
    _resolve_artifact_dir,
)

log = logging.getLogger("rice.platform")

app = FastAPI(title="rice-platform", version="0.1.0")

# CORS — rice-app renderer는 localhost에서 뜨므로 localhost 전체 허용
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0", "artifacts_dir": str(_artifacts_dir())}


# ── catalog (CONTRACTS §3) ────────────────────────────────────────────────────

@app.get("/catalog")
async def catalog(
    type: str | None = Query(None, description="skill | plugin"),
    q: str | None = Query(None, description="검색어 (name/display_name/description)"),
    category: str | None = Query(None),
    tags: list[str] | None = Query(None),
    sort: str = Query("popular", description="popular | recent | name"),
):
    """artifact 목록 반환 (RICE_CONTRACTS.md §3)."""
    items = list_artifacts(
        artifact_type=type,
        q=q,
        category=category,
        tags=tags,
        sort=sort,
    )
    return {"success": True, "data": items}


# ── single artifact ───────────────────────────────────────────────────────────

@app.get("/artifacts/{artifact_id}")
async def artifact_detail(artifact_id: str):
    item = get_artifact(artifact_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")
    return {"success": True, "data": item}


# ── download (CONTRACTS §2.3) ─────────────────────────────────────────────────

@app.get("/artifacts/{artifact_id}/files/{file_path:path}")
async def artifact_file(artifact_id: str, file_path: str):
    """artifact 내 개별 파일 내용 반환 (프론트 SkillFileBrowser 연결용).

    GET /artifacts/caveman/files/SKILL.md
    GET /artifacts/algorithmic-art/files/templates/viewer.html

    응답: { success, data: { path, content, language } }
    sandbox: artifact 루트 밖 경로 거부.
    """
    artifact_dir = _resolve_artifact_dir(artifact_id)
    if artifact_dir is None or not artifact_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")

    # sandbox: artifact 루트 밖 경로 거부
    target = (artifact_dir / file_path).resolve()
    resolved_root = artifact_dir.resolve()
    if resolved_root not in target.parents and target != resolved_root:
        raise HTTPException(status_code=403, detail="path escapes artifact root")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {file_path}")

    # 바이너리 파일(폰트, 이미지 등)은 내용 대신 메타만 반환
    binary_exts = {'.ttf', '.otf', '.woff', '.woff2', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf'}
    if target.suffix.lower() in binary_exts:
        return {"success": True, "data": {"path": file_path, "content": None, "language": "binary", "binary": True}}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    language = _language_from_path(file_path)
    return {"success": True, "data": {"path": file_path, "content": content, "language": language}}


def _language_from_path(path: str) -> str:
    """파일 경로에서 언어 추론 (프론트 SkillFileLanguage 타입과 일치)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    mapping = {
        "md": "markdown",
        "py": "python",
        "sh": "shell",
        "bash": "shell",
        "html": "html",
        "htm": "html",
        "json": "json",
        "js": "text",
        "ts": "text",
        "txt": "text",
        "yaml": "text",
        "yml": "text",
    }
    return mapping.get(ext, "text")


@app.get("/artifacts/{artifact_id}/download")
async def artifact_download(artifact_id: str):
    """artifact를 tar.gz로 스트리밍.

    응답 헤더:
        Content-Type: application/gzip
        X-Checksum: sha256-<hex>    (runtime이 검증에 사용)
    """
    result = build_download_package(artifact_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")

    data, checksum_hex = result
    filename = f"{artifact_id}.tar.gz"
    return Response(
        content=data,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Checksum": f"sha256-{checksum_hex}",
        },
    )


# ── sponsor (Blue Point mock, PLATFORM_DESIGN §6) ─────────────────────────────

# 메모리 내 포인트 집계 (재시작 시 초기화 — MVP)
_sponsor_totals: dict[str, int] = {}


@app.post("/artifacts/{artifact_id}/sponsor")
async def sponsor(artifact_id: str, request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    points = int(body.get("points", 100))
    if points <= 0:
        raise HTTPException(status_code=400, detail="points must be positive")
    if get_artifact(artifact_id) is None:
        raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")

    _sponsor_totals[artifact_id] = _sponsor_totals.get(artifact_id, 0) + points
    log.info("sponsor +%d → %s (total %d)", points, artifact_id, _sponsor_totals[artifact_id])
    return {"success": True, "data": {"artifact_id": artifact_id, "total_points": _sponsor_totals[artifact_id]}}


# ── publish (PLATFORM_DESIGN §5) ──────────────────────────────────────────────

@app.post("/artifacts")
async def publish(request: Request):
    """artifact 게시 (publish).

    MVP: multipart/form-data 대신 JSON manifest만 받아 artifacts/ 에 디렉토리를 생성한다.
    실제 파일 업로드(tar.gz)는 추후 multipart로 확장.

    Body (JSON):
        manifest: {dict}   — rice-artifact.yaml 내용
        files:    [{path, content}]  — artifact 파일 목록 (text 파일만 지원)
    """
    body: dict[str, Any] = await request.json()
    manifest: dict[str, Any] = body.get("manifest", {})
    files: list[dict[str, str]] = body.get("files", [])

    name = manifest.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="manifest.name is required")
    if manifest.get("type") not in ("skill", "plugin"):
        raise HTTPException(status_code=422, detail="manifest.type must be skill or plugin")

    artifact_dir = _artifacts_dir() / name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    # 샌드박스 비교는 양쪽 모두 resolve 한 경로로 한다 (artifact_file 핸들러와 동일 규칙).
    # 한쪽만 resolve 하면 심볼릭링크가 낀 경로에서 정상 파일이 조용히 누락될 수 있다.
    resolved_dir = artifact_dir.resolve()

    # manifest 저장
    with (artifact_dir / "rice-artifact.yaml").open("w", encoding="utf-8") as f:
        yaml.dump(manifest, f, allow_unicode=True, default_flow_style=False)

    # 첨부 파일 저장
    for file_entry in files:
        rel_path = file_entry.get("path", "").lstrip("/")
        content = file_entry.get("content", "")
        if not rel_path:
            continue
        target = (artifact_dir / rel_path).resolve()
        # sandbox: artifact_dir 밖으로 나가는 경로 거부
        if resolved_dir not in target.parents and target != resolved_dir:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    log.info("published artifact: %s (%s)", name, manifest.get("type"))
    return {"success": True, "data": {"id": name, "name": name, "version": manifest.get("version", "1.0.0")}}
