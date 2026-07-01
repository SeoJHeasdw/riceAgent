"""로컬 MCP 서버 지원 — `.mcp.json` 정의 + stdio 서브프로세스 실행 (connector.md Phase 2).

원격 커넥터(OAuth)와 달리, 로컬 MCP 서버는 사용자가 `.mcp.json` 에 선언하고 내 기기에서
직접 실행된다. 도구 표면(tools/list·tools/call)은 동일하므로 ConnectorService 가 원격과
같은 파이프라인(권한·dispatch)으로 흡수한다.
"""
from .config import LocalMcpServer, connector_id_to_name, load_local_mcp_servers

__all__ = ["LocalMcpServer", "load_local_mcp_servers", "connector_id_to_name"]
