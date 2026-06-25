# 작업: connector — 외부 연결 (Gmail OAuth · 키체인 · 권한)

> 먼저 `CONVENTIONS.md`를 읽었다고 가정한다.
> 정본: `../RICE_RUNTIME_DESIGN.md` §9 + `../RICE_CONTRACTS.md` §4.

## 목표

`IConnectorService` 뒤를 실제 구현으로 채운다. **프로토타입 대상은 Gmail 하나뿐**
(RUNTIME_DESIGN §9.1). 일반 MCP 어댑터는 Phase 2 — **만들지 마라.**

트리(RUNTIME_DIRECTORY §2):
```
connector/
  interfaces.py            # 동결 — 구현 대상 (읽기)
  service.py               # 파사드(IConnectorService) — 스텁 본문 교체
  manager.py               # #10 연결/해제 lifecycle
  oauth/                   # OAuth2 flow·refresh·redirect (__init__.py 필요)
  credential/keychain.py   # #11 OS 키체인(keyring)
  permission/checker.py    # #12 plugin→connector 접근 검증
  tests/
```

`service.py`는 현재 `(db)`만 받는다. 키체인·OAuth 설정이 필요하면
`composition_root.py`에서 DI 추가(허용).

## 동결된 계약 (구현할 것) — `connector/interfaces.py` 읽기

- 도구 표면(orchestrator용): `list_tools()`, `invoke(call)`, `check_permission(plugin_id, connector_type)`.
- 관리(routes/connectors.py용): `list_connectors()`, `get_connector(id)`, `status(id)`,
  `begin_connect(connector_type) -> OAuthStart`, `complete_oauth(code, state) -> ConnectorInfo`,
  `delete(id)`.
- DTO: `ConnectorInfo`, `OAuthStart`(둘 다 interfaces.py). 새로 만들지 마라.

## OAuth 2.0 흐름 (정본 RUNTIME_DESIGN §9.2, CONTRACTS §4.2)
```
begin_connect("gmail"):
  - state(CSRF 토큰) 생성, pending connector 레코드 생성(connectors 테이블, status='disconnected')
  - Google OAuth authorization URL 구성(scope 목록 포함) → OAuthStart(connector_id, oauth_url, state)
complete_oauth(code, state):
  - state 검증(위 토큰과 일치) → code를 access_token/refresh_token으로 교환
  - 토큰을 OS 키체인에 저장(아래) → connectors.status='connected', connected_at 갱신
  - connector_credentials에 메타(token_expires_at, scopes JSON)만 기록(토큰 본문 X)
  - ConnectorInfo 반환
```
- `connectors`, `connector_credentials` 테이블은 이미 0001 마이그레이션에 있다.
- Google client_id/secret/redirect_uri는 **설정/환경**에서 읽는다(하드코딩·커밋
  금지). 위치가 불확실하면 `# NOTE(agent):` 남기고 환경변수에서 읽어라.
- HTTP 호출은 `httpx`(optional extra `connectors`). 지연 import.

## Credential 저장 (정본 §9.3) — credential/keychain.py
- **OS 키체인만.** 라이브러리 `keyring`(extra `connectors`, 지연 import).
- 키 형식 규약: `rice.connector.{connector_id}.{credential_type}`(§9.3).
- 저장 항목: access_token, refresh_token, expires_at. **DB·로그·평문파일 금지.**
- 자동 갱신: 만료 임박 시 refresh_token으로 갱신하는 함수 제공(백그라운드 스케줄링은
  MVP 밖 — 함수만 만들고 `invoke`/`status` 시 필요하면 호출).

## 권한 검증 (정본 §9.4, CONTRACTS §4.4) — permission/checker.py
```python
def check_plugin_permission(plugin_id, connector_type) -> bool:
    # plugin manifest의 requires.connectors(= connector_type 목록)에 포함되는지
```
- `connector_type`("gmail")과 인스턴스 `connector_id`를 **혼동하지 마라**(§9.4).
- plugin manifest 접근이 필요한데 execution 모듈 구체에 의존하면 안 된다 → 필요한
  최소 정보를 인자로 받거나, manifest를 직접 파일에서 읽어라(`~/.rice/plugins/`).
  애매하면 NOTE 후 **fail-closed(False)**.

## 도구 표면 (orchestrator용)
- `list_tools()`: 연결된(connected) connector가 제공하는 `ToolSpec`들. Gmail
  프로토타입의 atomic tool 몇 개(예: 메일 검색/읽기)를 `source="connector"`,
  `requires_approval=True`로 노출(외부 접근이므로 승인 대상 — RUNTIME_DESIGN §5).
- `invoke(call)`: 권한 + (orchestrator가 이미 HITL 승인 통과 후 호출) → 실제 Gmail
  API 호출 → `ToolResult`. 실패는 예외가 아니라 `ToolResult(ok=False, error=...)`.

## 수용 기준 (Acceptance) — 네트워크 없이 검증 가능하게
1. `begin_connect("gmail")`이 state 포함 URL과 pending 레코드를 만든다(HTTP 모킹).
2. `complete_oauth`가 state 불일치를 거부하고, 일치 시 키체인 저장 + status=connected
   (keyring·httpx 모킹).
3. `check_plugin_permission`: requires.connectors에 gmail 있으면 True, 없으면 False.
4. 키/토큰이 **로그·DB·반환 JSON에 노출되지 않음**(테스트로 확인).
5. walking_skeleton.py 정상.

## 하지 말 것
- Gmail 외 connector 추가 금지. 일반 MCP 어댑터 금지.
- 토큰을 DB/로그/파일 평문 저장 금지(키체인만).
- `interfaces.py`/계약 변경 금지(엔드포인트는 이미 routes/connectors.py에 있음).
