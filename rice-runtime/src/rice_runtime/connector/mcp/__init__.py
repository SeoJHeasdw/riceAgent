"""Remote MCP transport for connectors (connector.md 도구 표면).

connector 가 노출하는 도구는 하드코딩하지 않는다 — 원격 MCP 서버의 `tools/list`
응답을 그대로 ToolSpec 으로 변환해 동적으로 노출한다. `RemoteMcpClient` 참고.
"""
from .client import RemoteMcpClient

__all__ = ["RemoteMcpClient"]
