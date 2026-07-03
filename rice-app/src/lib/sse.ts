import type { SSEEvent, SSEEventType } from './types';

/**
 * SSE client for the RICE runtime (RICE_CONTRACTS 1.2/1.3).
 *
 * The contract streams a turn over `POST /sessions/{id}/messages` → SSE. Browser
 * `EventSource` is GET-only, so streaming uses fetch + a ReadableStream reader and
 * parses SSE frames manually. Events are dispatched as the typed `SSEEvent` union
 * (9 stream events + 2 control events) so callers switch on `event.type`.
 *
 * Approvals are out-of-band: the stream emits `tool_approval_required` /
 * `skill_patch_proposed` (each with an `approval_id`); the app responds via a
 * separate POST (lib/api → respondToolApproval / respondSkillPatch). Parking,
 * timeout, and reconnect recovery (GET /pending-approvals) are the runtime's job
 * (RUNTIME_DESIGN 5.1) — the client just renders the prompt and posts the answer.
 */

const STREAM_EVENT_TYPES: ReadonlySet<string> = new Set<SSEEventType>([
  'thinking',
  'tool_call',
  'tool_result',
  'message',
  'memory_written',
  'skill_patched',
  'done',
  'error',
  'tool_approval_required',
  'skill_patch_proposed',
]);

export interface StreamHandlers {
  onEvent: (event: SSEEvent) => void;
  onOpen?: () => void;
  /** transport-level failure (network / non-2xx), distinct from an `error` event */
  onError?: (message: string) => void;
  /** stream closed (after `done`, abort, or EOF) */
  onClose?: () => void;
}

export interface StreamHandle {
  /** abort the in-flight turn (also call POST /sessions/{id}/abort separately) */
  abort: () => void;
}

/** Coerce a parsed SSE frame into a typed SSEEvent (drops unknown types). */
function toSSEEvent(type: string, raw: unknown): SSEEvent | null {
  if (!STREAM_EVENT_TYPES.has(type)) return null;
  const data = raw && typeof raw === 'object' ? raw : {};
  return { type, data } as SSEEvent;
}

/**
 * Parse a single SSE frame (lines between blank lines). Supports both
 *   event: <type>\n data: <json>
 * and a bare
 *   data: {"type": "...", ...}
 * form, so the client is robust to either runtime encoding.
 */
function parseFrame(frame: string): SSEEvent | null {
  let eventName = '';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue; // comment / keep-alive
    if (line.startsWith('event:')) eventName = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
  }
  if (dataLines.length === 0 && !eventName) return null;

  const dataStr = dataLines.join('\n');
  let parsed: unknown = dataStr;
  try {
    parsed = dataStr ? JSON.parse(dataStr) : {};
  } catch {
    parsed = { content: dataStr };
  }

  // bare form: { type, data?: {...}, ...rest }
  if (!eventName && parsed && typeof parsed === 'object' && 'type' in parsed) {
    const obj = parsed as { type: string; data?: unknown };
    return toSSEEvent(obj.type, obj.data ?? obj);
  }
  return toSSEEvent(eventName || 'message', parsed);
}

export class SSEClient {
  private baseUrl: string;
  private token: string | null = null;
  /** off in the design demo (no runtime) — see ApiClient.enabled */
  private enabled = false;

  constructor(baseUrl: string = 'http://localhost:8080') {
    this.baseUrl = baseUrl;
  }

  setBaseUrl(url: string) {
    this.baseUrl = url;
  }

  setToken(token: string | null) {
    this.token = token;
  }

  setEnabled(v: boolean) {
    this.enabled = v;
  }

  /**
   * Stream a turn: POST the message and consume the SSE response.
   * `body` is the message payload (text + optional attachments — CONTRACTS 1-C).
   */
  streamMessages(sessionId: string, body: unknown, handlers: StreamHandlers): StreamHandle {
    return this._streamPost(`/sessions/${sessionId}/messages`, body, handlers);
  }

  /** re-answer the last user message — streams a fresh turn (POST /regenerate). */
  regenerate(sessionId: string, handlers: StreamHandlers): StreamHandle {
    return this._streamPost(`/sessions/${sessionId}/regenerate`, undefined, handlers);
  }

  /**
   * Gated LLM skill/memory review — the client calls this fire-and-forget right
   * after the main turn's `done`, same trigger point as the title call (Phase A).
   * 서버가 턴 종료 시 TurnOutcome(tool_calls_made 포함)을 learning_jobs에
   * 직접 영속화하고 리뷰를 exactly-once로 claim해 실행한다 — 클라이언트는 더 이상
   * 아무것도 세지 않는다. 중복 호출·재시도·탭 종료 모두 안전(미수거 잡은 백그라운드
   * sweep이 제안함으로 라우팅). Fast no-op (single `done` frame) when there is no
   * pending job or the server-side gate declines.
   */
  reviewLearning(sessionId: string, handlers: StreamHandlers): StreamHandle {
    return this._streamPost(`/sessions/${sessionId}/learning-review`, {}, handlers);
  }

  /** Explicit "이 대화를 스킬로 저장" action (Phase C) — always attempts, no gate. */
  learnFromConversation(sessionId: string, instruction: string, handlers: StreamHandlers): StreamHandle {
    return this._streamPost(`/sessions/${sessionId}/learn`, { instruction }, handlers);
  }

  /** Shared SSE-over-POST driver. `body===undefined` posts with no JSON body. */
  private _streamPost(path: string, body: unknown, handlers: StreamHandlers): StreamHandle {
    if (!this.enabled) {
      handlers.onError?.('runtime offline');
      return { abort: () => {} };
    }
    const controller = new AbortController();

    const run = async () => {
      let opened = false;
      try {
        const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'text/event-stream' };
        if (this.token) headers['Authorization'] = `Bearer ${this.token}`;

        const res = await fetch(`${this.baseUrl}${path}`, {
          method: 'POST',
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) {
          handlers.onError?.(`HTTP ${res.status}`);
          return;
        }
        opened = true;
        handlers.onOpen?.();

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // dispatch complete frames (separated by a blank line)
          let sep: number;
          while ((sep = buffer.search(/\r?\n\r?\n/)) !== -1) {
            const frame = buffer.slice(0, sep);
            buffer = buffer.slice(sep + (buffer[sep] === '\r' ? 4 : 2));
            const event = parseFrame(frame);
            if (event) handlers.onEvent(event);
          }
        }
        const tail = parseFrame(buffer);
        if (tail) handlers.onEvent(tail);
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') return; // expected on abort()
        handlers.onError?.(err instanceof Error ? err.message : 'stream failed');
      } finally {
        if (opened) handlers.onClose?.();
      }
    };

    void run();
    return { abort: () => controller.abort() };
  }

  /** convenience: text-only message */
  streamText(sessionId: string, text: string, handlers: StreamHandlers): StreamHandle {
    return this.streamMessages(sessionId, { content: text }, handlers);
  }
}

export const sseClient = new SSEClient();
export default sseClient;

// Made with Bob
