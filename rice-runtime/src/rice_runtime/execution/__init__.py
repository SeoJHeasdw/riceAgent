"""Execution module — plugin loading (#7) and sandbox boundary (#8).

Trust model (RUNTIME_DESIGN §5): market *skills* are declarative (SKILL.md,
no code) → no sandbox. Only plugin/connector *code* executes, in a child
process with a scrubbed environment (ChildProcessSandbox).
"""
