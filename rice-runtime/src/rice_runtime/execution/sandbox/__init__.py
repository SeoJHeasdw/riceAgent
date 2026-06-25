"""Sandbox sub-package: child-process execution boundary (RUNTIME_DESIGN §5).

policy.py  — scrubs the environment and applies resource limits
ipc.py     — JSON-line stdin/stdout protocol between parent and child
runner.py  — spawns the child, enforces timeout, collects result
"""
