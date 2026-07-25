"""번들 로컬 Gmail MCP 서버 (stdio, newline-delimited JSON-RPC).

왜 존재하나: Google 이 호스팅하는 원격 Gmail MCP(gmailmcp.googleapis.com)는 Workspace
Developer Preview 승인제라 tools/call 이 막힐 수 있다. 반면 **Gmail REST API 자체는
게이트가 없다** — RICE 의 gmail 커넥터가 OAuth 로 이미 확보한 키체인 자격증명을 그대로
재사용해, 이 서버가 REST API 를 직접 호출한다. 추가 브라우저 인증·Google 콘솔 설정 0회.

실행: `.mcp.json` 에 등록 (spawn-per-op — stdio_client.py 규약)
    {"mcpServers": {"gmail-local": {
        "command": "<venv>/bin/python",
        "args": ["-m", "rice_runtime.connector.local.gmail_mcp"]}}}

계약:
  - stdout 은 JSON-RPC 응답 전용. 로그는 전부 stderr (stdio_client 가 비-JSON 라인을
    무시하긴 하지만, 규약 위반을 만들지 않는다).
  - 자격증명은 RICE state.db(read-only) 에서 gmail connector id 를 찾고 키체인에서
    읽는다. 만료 시 이 프로세스 안에서만 refresh (키체인에 되쓰지 않는다 — 소유자는
    런타임. Google web client 의 refresh_token 은 기본 비-rotation이라 공유 안전).
  - 도구 실패는 JSON-RPC error 가 아니라 MCP 규약의 result.isError 로 돌려준다
    (stdio_client.call_tool 이 그렇게 해석한다).
  - 쓰기 도구는 create_draft 뿐(발송 없음) — 데모 안전 범위.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sqlite3
import sys
from email.message import EmailMessage
from typing import Any

log = logging.getLogger("rice.connector.local.gmail_mcp")

_PROTOCOL_VERSION = "2025-06-18"
_SERVER_INFO = {"name": "rice-gmail-local", "version": "0.1.0"}
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_MAX_BODY_CHARS = 20_000
_MAX_RESULTS_CAP = 25

# 원격 gmail MCP 카탈로그와 이름이 겹치지 않게 gmail_ 접두 — 이름 재탐색 라우팅의
# 로컬 우선 규칙이 원격 도구를 가리는 사고를 피한다(둘 다 연결된 경우).
TOOLS: list[dict[str, Any]] = [
    # `keywords` 는 MCP 표준 밖의 확장 필드다(mcp/client.py `_raw_to_spec` 가 읽는다).
    # 라우터 전용 트리거 어휘이고 모델 스키마에는 실리지 않으므로, 설명문을 한국어로
    # 오염시키지 않고 한국어 요청이 이 도구에 닿게 한다. 전역 intent 테이블은 범용
    # 동사(메일→mail 등)만 덮으므로 도메인어(라벨·초안·받은편지함)는 여기서만 닿는다.
    {
        "name": "gmail_list_labels",
        "description": "List the Gmail labels of the connected account.",
        "keywords": ["라벨", "메일", "지메일", "label", "gmail"],
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "gmail_search_messages",
        "description": (
            "Search Gmail messages with the standard Gmail query syntax "
            "(e.g. 'from:billing@example.com newer_than:7d'). "
            "Returns id, from, subject, date and snippet for each match."
        ),
        "keywords": ["메일", "지메일", "이메일", "받은편지함", "검색", "찾기", "gmail", "inbox"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query (q=)"},
                "max_results": {
                    "type": "integer",
                    "description": f"Maximum messages to return (default 10, max {_MAX_RESULTS_CAP})",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_get_message",
        "description": "Read one Gmail message by id — headers plus the plain-text body.",
        "keywords": ["메일", "지메일", "이메일", "본문", "읽기", "내용", "gmail"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message id from gmail_search_messages"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": (
            "Create a Gmail draft (does NOT send). "
            "The user reviews and sends it from Gmail themselves."
        ),
        "keywords": ["메일", "지메일", "이메일", "초안", "답장", "회신", "작성", "보내기", "gmail", "draft"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]


class GmailAuthError(RuntimeError):
    """자격증명 없음/만료·갱신 불가 — 사용자에게 RICE 재연결을 안내해야 하는 상태."""


class GmailCredentialSource:
    """RICE 가 확보해 둔 gmail OAuth 자격증명의 읽기 전용 소비자."""

    def __init__(self) -> None:
        self._access_token: str | None = None

    async def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        from ...core.paths import RicePaths
        from ..credential.keychain import KeychainStore

        paths = RicePaths.from_env()
        connector_id = self._gmail_connector_id(paths.db)
        if connector_id is None:
            raise GmailAuthError(
                "No Gmail connector found. Connect Gmail in RICE (Connectors screen) first."
            )
        credential = KeychainStore().load(connector_id)
        if credential is None:
            raise GmailAuthError(
                "Gmail credentials are missing from the keychain. Reconnect Gmail in RICE."
            )
        if not credential.is_expired():
            self._access_token = credential.access_token
            return self._access_token
        if not credential.refresh_token:
            raise GmailAuthError("Gmail access token expired and no refresh token. Reconnect Gmail in RICE.")

        from ..oauth.flow import OAuthFlow
        from ..registry import GMAIL_DESCRIPTOR

        flow = OAuthFlow(GMAIL_DESCRIPTOR, connectors_dir=paths.connectors)
        try:
            refreshed = await flow.refresh_access_token(credential.refresh_token)
        except Exception as exc:  # noqa: BLE001
            raise GmailAuthError(f"Gmail token refresh failed ({exc}). Reconnect Gmail in RICE.") from exc
        self._access_token = refreshed.access_token
        return self._access_token

    @staticmethod
    def _gmail_connector_id(db_path) -> str | None:
        """state.db 를 read-only 로 열어 최신 gmail connector id 를 찾는다.

        status 는 보지 않는다 — 원격 MCP 실행 검증 실패(error)여도 OAuth 토큰은
        유효할 수 있고, 그 토큰이 바로 이 서버의 존재 이유다.
        """
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT id FROM connectors WHERE connector_type='gmail' "
                    "ORDER BY connected_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            log.warning("cannot read RICE db %s: %s", db_path, exc)
            return None
        return row[0] if row else None


# ── Gmail REST 핸들러 ─────────────────────────────────────────────────────────
async def _api_get(client, token: str, path: str, params: dict | None = None) -> dict:
    resp = await client.get(
        f"{_GMAIL_API}{path}", params=params, headers={"Authorization": f"Bearer {token}"}
    )
    _raise_for_api_error(resp)
    return resp.json()


def _raise_for_api_error(resp) -> None:
    if resp.status_code < 400:
        return
    body = resp.text.strip()[:400]
    raise RuntimeError(f"Gmail API HTTP {resp.status_code}: {body}")


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if str(h.get("name", "")).lower() == name.lower():
            return str(h.get("value", ""))
    return ""


def _extract_text(payload: dict) -> str:
    """message payload 트리에서 text/plain 을 우선 수집한다(없으면 text/html 원문)."""
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data:
            try:
                text = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
            except (ValueError, TypeError):
                text = ""
            if mime == "text/plain":
                plain.append(text)
            elif mime == "text/html":
                html.append(text)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return ("\n".join(plain) or "\n".join(html))[:_MAX_BODY_CHARS]


async def handle_tool(name: str, args: dict[str, Any], creds: GmailCredentialSource, transport=None) -> Any:
    """도구 1건 실행 → JSON 직렬화 가능한 결과. 실패는 예외로 던진다.

    transport 는 테스트 전용 seam(httpx.MockTransport) — 운영은 None.
    """
    import httpx

    token = await creds.access_token()
    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        if name == "gmail_list_labels":
            data = await _api_get(client, token, "/labels")
            return [
                {"id": l.get("id"), "name": l.get("name"), "type": l.get("type")}
                for l in data.get("labels", [])
            ]

        if name == "gmail_search_messages":
            query = str(args.get("query", "")).strip()
            if not query:
                raise ValueError("query is required")
            n = min(int(args.get("max_results") or 10), _MAX_RESULTS_CAP)
            listing = await _api_get(client, token, "/messages", {"q": query, "maxResults": n})
            out = []
            for ref in listing.get("messages", [])[:n]:
                msg = await _api_get(
                    client, token, f"/messages/{ref['id']}",
                    {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                )
                payload = msg.get("payload", {})
                out.append({
                    "id": msg.get("id"),
                    "from": _header(payload, "From"),
                    "subject": _header(payload, "Subject"),
                    "date": _header(payload, "Date"),
                    "snippet": msg.get("snippet", ""),
                })
            return {"query": query, "count": len(out), "messages": out}

        if name == "gmail_get_message":
            message_id = str(args.get("message_id", "")).strip()
            if not message_id:
                raise ValueError("message_id is required")
            msg = await _api_get(client, token, f"/messages/{message_id}", {"format": "full"})
            payload = msg.get("payload", {})
            return {
                "id": msg.get("id"),
                "from": _header(payload, "From"),
                "to": _header(payload, "To"),
                "subject": _header(payload, "Subject"),
                "date": _header(payload, "Date"),
                "body": _extract_text(payload),
            }

        if name == "gmail_create_draft":
            to = str(args.get("to", "")).strip()
            subject = str(args.get("subject", ""))
            body = str(args.get("body", ""))
            if not to:
                raise ValueError("to is required")
            mime = EmailMessage()
            mime["To"] = to
            mime["Subject"] = subject
            mime.set_content(body)
            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
            resp = await client.post(
                f"{_GMAIL_API}/drafts",
                json={"message": {"raw": raw}},
                headers={"Authorization": f"Bearer {token}"},
            )
            _raise_for_api_error(resp)
            data = resp.json()
            return {"draft_id": data.get("id"), "message_id": (data.get("message") or {}).get("id")}

    raise ValueError(f"unknown tool {name!r}")


# ── JSON-RPC (stdio) 디스패치 ─────────────────────────────────────────────────
def handle_rpc_sync(msg: dict[str, Any]) -> dict[str, Any] | None:
    """tools/call 이전의 프로토콜 메시지 처리. 응답 없으면 None(알림)."""
    method = msg.get("method")
    req_id = msg.get("id")
    if req_id is None:  # notification (notifications/initialized 등)
        return None
    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": _SERVER_INFO,
        })
    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        return {}  # sentinel — 호출자가 async 경로로 처리
    return {
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


async def handle_tool_call(msg: dict[str, Any], creds: GmailCredentialSource, transport=None) -> dict[str, Any]:
    params = msg.get("params") or {}
    name = str(params.get("name", ""))
    args = params.get("arguments") or {}
    try:
        result = await handle_tool(name, args, creds, transport=transport)
        text = json.dumps(result, ensure_ascii=False)
        return _result(msg.get("id"), {"content": [{"type": "text", "text": text}]})
    except Exception as exc:  # noqa: BLE001 — 도구 실패는 isError 로 정규화(MCP 규약)
        log.warning("tool %s failed: %s", name, exc)
        return _result(msg.get("id"), {
            "isError": True,
            "content": [{"type": "text", "text": str(exc)}],
        })


def _result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


async def serve() -> None:
    """stdin 라인 루프 — spawn-per-op 라 요청 몇 건 처리 후 EOF 로 끝난다."""
    creds = GmailCredentialSource()
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return  # EOF — 클라이언트가 stdin 을 닫음
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log.warning("non-JSON line on stdin ignored")
            continue
        response = handle_rpc_sync(msg)
        if response == {}:  # tools/call sentinel
            response = await handle_tool_call(msg, creds)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
