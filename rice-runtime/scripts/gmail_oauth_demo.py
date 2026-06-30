"""Gmail Connector 실연(實演) 하니스 — 실제 브라우저 로그인 → 연결 → 메일 발송.

RICE 의 Gmail Connector(OAuth + 직접 REST, CONTRACTS §4)가 사용자 PC 에서 **진짜로**
동작하는지 한 방에 검증한다. 목업 프론트엔드/LLM 없이 다음을 실제로 수행한다:

  1. Google OAuth client JSON 로드(Desktop "installed" 형식).
  2. 임시 loopback HTTP 서버를 띄우고(127.0.0.1:임의포트), 시스템 기본 브라우저를 연다.
  3. 사용자가 브라우저에서 Google 로그인·동의 → 콜백으로 authorization code 수신.
  4. code → 토큰 교환(Authorization Code Flow).
  5. 토큰을 **OS 키체인**에 저장 + state.db 의 connectors/connector_credentials 갱신
     (→ 이후 RICE 에이전트가 같은 연결로 gmail_search/read/send 사용 가능).
  6. (옵션) --send-to 로 실제 테스트 메일 발송 + 프로필 조회로 연결 확인.

사용 예:
    # 연결만 (브라우저 로그인 → 키체인/DB 저장)
    python scripts/gmail_oauth_demo.py \
        --client-secret ../client_secret_***.json

    # 연결 + 본인에게 테스트 메일 발송
    python scripts/gmail_oauth_demo.py \
        --client-secret ../client_secret_***.json --send-to me@gmail.com

    # client JSON 을 ~/.rice/connectors/ 에 설치(런타임이 이후 자동 사용)
    python scripts/gmail_oauth_demo.py --client-secret ../client_secret_***.json --install-secret

토큰 본문은 화면/로그에 절대 출력하지 않는다 (RUNTIME_DESIGN §10.4).
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import shutil
import sys
import threading
import urllib.parse
import webbrowser
from email.message import EmailMessage
from pathlib import Path

# rice_runtime 패키지를 import 경로에 올린다 (scripts/ 는 패키지 밖).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import httpx  # noqa: E402  (connectors extra)

from rice_runtime.connector.credential.keychain import KeychainStore  # noqa: E402
from rice_runtime.connector.oauth.flow import (  # noqa: E402
    GMAIL_SCOPES,
    GmailOAuthFlow,
    _load_client_config_from_json,
)
from rice_runtime.core.paths import RicePaths  # noqa: E402
from rice_runtime.core.storage.sqlite import Database  # noqa: E402
from rice_runtime.core.types import now_iso  # noqa: E402

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DEFAULT_CONNECTOR_ID = "conn_gmail_cli"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """loopback 콜백 1회 처리 — code/state 를 서버 객체에 담는다."""

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.oauth_code = (params.get("code") or [None])[0]  # type: ignore[attr-defined]
        self.server.oauth_state = (params.get("state") or [None])[0]  # type: ignore[attr-defined]
        self.server.oauth_error = (params.get("error") or [None])[0]  # type: ignore[attr-defined]
        ok = self.server.oauth_code is not None and self.server.oauth_error is None  # type: ignore[attr-defined]
        body = (
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
            f"<h2>{'✓ 연결 완료' if ok else '✕ 연결 실패'}</h2>"
            "<p>이 창을 닫고 터미널로 돌아가세요.</p></body></html>"
        ).encode("utf-8")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # 콜백 서버 로그 소거
        pass


def _run_oauth(client_id: str, client_secret: str) -> tuple[str, str | None, str, int]:
    """브라우저 OAuth 를 수행하고 (access, refresh, scope, expires_in) 를 반환한다(loopback)."""
    # 임시 포트로 콜백 서버 — Desktop 자격증명은 loopback 임의 포트/경로를 허용한다.
    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    server.oauth_code = server.oauth_state = server.oauth_error = None  # type: ignore[attr-defined]
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}/"

    flow = GmailOAuthFlow(
        client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri
    )
    auth_url, state = flow.build_auth_url()

    print(f"\n▶ 브라우저를 엽니다 (콜백: {redirect_uri})")
    print("  열리지 않으면 아래 URL 을 직접 붙여넣으세요:\n")
    print(f"  {auth_url}\n")
    webbrowser.open(auth_url)

    # 콜백 1건 처리 (블로킹).
    print("  브라우저에서 로그인·동의를 기다리는 중…")
    server.handle_request()

    if server.oauth_error:  # type: ignore[attr-defined]
        raise SystemExit(f"✕ 인증 거부됨: {server.oauth_error}")  # type: ignore[attr-defined]
    code = server.oauth_code  # type: ignore[attr-defined]
    got_state = server.oauth_state  # type: ignore[attr-defined]
    if not code:
        raise SystemExit("✕ authorization code 를 받지 못했습니다.")
    if got_state != state:
        raise SystemExit("✕ state 불일치 — CSRF 의심으로 중단합니다.")

    # code → token (동기 httpx).
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("refresh_token"), data.get("scope", ""), data.get("expires_in", 3600)


def _persist(paths: RicePaths, connector_id: str, access: str, refresh: str | None, expires_in: int, scope: str) -> None:
    """토큰을 키체인에, 메타를 state.db 에 저장한다(런타임/에이전트가 같은 연결 사용)."""
    KeychainStore().save(connector_id, access_token=access, refresh_token=refresh, expires_in=expires_in)

    db = Database(paths.db).connect()
    now = now_iso()
    db.execute(
        "INSERT INTO connectors(id, connector_type, display_name, status, connected_at, last_verified_at) "
        "VALUES (?, 'gmail', 'Gmail', 'connected', ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET status='connected', connected_at=excluded.connected_at, "
        "last_verified_at=excluded.last_verified_at",
        (connector_id, now, now),
    )
    db.execute(
        "INSERT INTO connector_credentials(connector_id, token_expires_at, scopes) VALUES (?, ?, ?) "
        "ON CONFLICT(connector_id) DO UPDATE SET token_expires_at=excluded.token_expires_at, scopes=excluded.scopes",
        (connector_id, None, json.dumps(scope.split()) if scope else "[]"),
    )
    db.close()


def _profile(access: str) -> dict:
    resp = httpx.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {access}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _send(access: str, to: str, subject: str, body: str) -> dict:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    resp = httpx.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
        json={"raw": raw},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    ap = argparse.ArgumentParser(description="RICE Gmail Connector 실연 하니스")
    ap.add_argument("--client-secret", help="Google OAuth client JSON 경로 (installed 형식)")
    ap.add_argument("--connector-id", default=_DEFAULT_CONNECTOR_ID, help="저장할 connector_id")
    ap.add_argument("--send-to", help="테스트 메일 받는 주소(지정 시 실제 발송)")
    ap.add_argument("--install-secret", action="store_true",
                    help="client JSON 을 ~/.rice/connectors/gmail_client_secret.json 으로 복사")
    args = ap.parse_args()

    paths = RicePaths.from_env().ensure()

    # ── client 자격증명 로드 ─────────────────────────────────────────────────
    if args.client_secret:
        cfg = _load_client_config_from_json(Path(args.client_secret).expanduser())
        if cfg is None:
            raise SystemExit(f"✕ client JSON 을 읽지 못했습니다: {args.client_secret}")
    else:
        from rice_runtime.connector.oauth.flow import load_gmail_client_config
        cfg = load_gmail_client_config(paths.connectors)
        if cfg is None:
            raise SystemExit("✕ 자격증명이 없습니다. --client-secret 로 JSON 을 지정하세요.")

    print(f"  client_id: …{cfg.client_id[-24:]}")
    print(f"  scopes:    {', '.join(s.rsplit('/', 1)[-1] for s in GMAIL_SCOPES)}")

    # ── OAuth ────────────────────────────────────────────────────────────────
    access, refresh, scope, expires_in = _run_oauth(cfg.client_id, cfg.client_secret)
    print("\n✓ 토큰 교환 성공 (토큰 본문은 출력하지 않음)")
    print(f"  refresh_token 발급: {'예' if refresh else '아니오 (이미 동의한 계정일 수 있음)'}")

    # ── 영속화 ────────────────────────────────────────────────────────────────
    _persist(paths, args.connector_id, access, refresh, int(expires_in), scope)
    print(f"✓ 키체인 + state.db 저장 완료 (connector_id={args.connector_id})")

    if args.install_secret and args.client_secret:
        dest = paths.connectors / "gmail_client_secret.json"
        shutil.copy2(args.client_secret, dest)
        print(f"✓ client JSON 설치: {dest}")

    # ── 연결 확인 + (옵션) 발송 ─────────────────────────────────────────────
    prof = _profile(access)
    print(f"\n✓ 연결 확인 — 계정: {prof.get('emailAddress')} (총 메시지 {prof.get('messagesTotal')}건)")

    if args.send_to:
        result = _send(
            access,
            args.send_to,
            subject="[RICE] Gmail Connector 연결 테스트",
            body="이 메일은 RICE Gmail Connector 가 실제로 발송했습니다. 🎉\n\n— gmail_oauth_demo.py",
        )
        print(f"✓ 테스트 메일 발송 완료 → {args.send_to} (message id: {result.get('id')})")

    print("\n완료. 이제 RICE 에이전트가 gmail_search / gmail_read / gmail_send 를 사용할 수 있습니다.")


if __name__ == "__main__":
    main()
