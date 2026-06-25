# rice-runtime

RICE's local single-process **brain layer** (배포물 ②). A *modular monolith*: six
modules wired by **one** composition root, talking **only** through `interfaces.py`
Protocols. The point of this skeleton is the **seams**, not the features — the
feature bodies are stubs marked `TODO(module)` behind frozen interfaces.

> Canonical module map: [`RICE_RUNTIME_DIRECTORY.md`](../RICE_RUNTIME_DIRECTORY.md).
> Wire contract: [`RICE_CONTRACTS.md`](../RICE_CONTRACTS.md) + `contracts/`.

## Run the walking skeleton (no deps)

```bash
python3 walking_skeleton.py
```

Drives the **full turn path** with the offline `echo` provider — no FastAPI, no API
key — and prints every SSE event:

```
composition_root → orchestrator.run_turn → learning.prefetch → AgentLoop
  → EchoProvider → SSE(thinking, message…, done) → session persisted
```

## Serve the real local API

```bash
pip install -e .            # fastapi, uvicorn, pydantic, pyyaml
rice-runtime --port 8080 --token <one-shot>
curl localhost:8080/health
```

The Electron supervisor normally spawns this with a dynamic `--port` and one-shot
`--token` (CONTRACTS §1.1). All paths except `/health` require `Authorization:
Bearer <token>`.

## The three invariants (RUNTIME_DIRECTORY §5)

1. **No cross-module imports** — a module reaches another only via its
   `interfaces.py` Protocol, never its implementation.
2. **`server/composition_root.py` is the only wiring point** — the sole place that
   imports concrete classes and `new`s them. Swap an impl here, nowhere else.
3. **Protocol-based interfaces** — each module exposes Protocols; impls stay
   encapsulated, so a module can later be split into its own process.

Quick check that #1/#2 hold:

```bash
grep -rn --include="*.py" -E \
  "^from \.\.(learning|connector|execution|agent)\.(service|loop|orchestrator|manager|store|loader)" \
  src/rice_runtime | grep -v composition_root.py    # → no matches
```

## The convergence point

[`agent/orchestrator.py`](src/rice_runtime/agent/orchestrator.py) is where
agent ↔ learning ↔ connector ↔ execution meet. It is deliberately **thin** — it
*sequences* a turn; each step's behavior lives in the owning module:

```
run_turn(session, msg):
  persist user → learning.prefetch → assemble tools+prompt → AgentLoop(provider)
    → tool dispatch (+ HITL approval) → persist assistant → learning.observe
    → surface effects (patch → HITL gate → apply) → done
```

## Layout

```
contracts/                 runtime.yaml (OpenAPI) · sse-events.schema.json   ← owned here, app codegens
src/rice_runtime/
  core/        types(DTOs across seams) · storage(sqlite+FTS5) · paths · config · events · errors
  agent/       interfaces · orchestrator(convergence) · loop · tool_dispatch · providers/{echo,registry,…}
  learning/    interfaces(ILearningService) · service(stub)        ← memory/skill/curator go behind this
  connector/   interfaces(IConnectorService) · service(stub)       ← oauth/keychain/permission
  execution/   interfaces(IPluginLoader/ISandbox) · service(stub)  ← plugin loader + child-proc sandbox
  server/      app · composition_root · approval(HITL) · sse · session · routes/
walking_skeleton.py        end-to-end demo (stdlib only)
tests/                     turn + approval-mechanism tests
```

## What's real vs stub in this skeleton

| Real (runnable now)                              | Stub (`TODO(module)`, behind interface) |
|--------------------------------------------------|------------------------------------------|
| core/* (storage, types, config, paths, events)   | learning: prefetch/observe/skills/patch  |
| orchestrator + loop + tool_dispatch + HITL gate  | connector: OAuth / keychain / tools      |
| session store (sqlite + FTS5)                    | execution: plugin loader + sandbox       |
| FastAPI app + routes + auth + SSE emitter        | claude/ollama providers (echo only)      |
| contracts (OpenAPI + SSE JSON Schema)            |                                          |

Each stub is the **drop-in seam**: implement the module behind its Protocol and the
orchestrator/server never change.
```
