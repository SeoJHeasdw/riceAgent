import type {
  ApiResponse,
  Plugin,
  ImpactReport,
  PendingApproval,
  ToolApprovalDecision,
  SkillPatchDecision,
} from './types';

/**
 * API client for the RICE runtime — aligned to RICE_CONTRACTS 1.3 endpoints.
 *
 * The runtime owns `contracts/runtime.yaml` (REPO_STRATEGY 4.3); this client is
 * the consumer side. Paths here mirror that spec so the app is wire-ready: when
 * the local runtime is up, these resolve; until then the screens fall back to
 * the scripted mock (lib/learningData). Transport is localhost HTTP; streaming
 * (POST /sessions/{id}/messages → SSE) lives in lib/sse.ts.
 */
class ApiClient {
  private baseUrl: string;
  /** one-shot local auth token issued by Electron main on spawn (CONTRACTS 1.1) */
  private token: string | null = null;
  /** is a local runtime actually connected? Off in the design demo (no runtime
   *  spawned) so we make no failing requests — calls resolve to a no-op result
   *  and screens fall back to scripted mock data. Flipped on at startup once
   *  Electron reports the runtime running (App bootstrap). */
  private enabled = false;

  constructor(baseUrl: string = 'http://localhost:8080') {
    this.baseUrl = baseUrl;
  }

  setBaseUrl(url: string) {
    this.baseUrl = url;
  }

  setEnabled(v: boolean) {
    this.enabled = v;
  }

  get isEnabled(): boolean {
    return this.enabled;
  }

  /** localhost auth token (CONTRACTS 1.1) — sent as a bearer header */
  setToken(token: string | null) {
    this.token = token;
  }

  get base(): string {
    return this.baseUrl;
  }

  private headers(json = false): HeadersInit {
    const h: Record<string, string> = {};
    if (json) h['Content-Type'] = 'application/json';
    if (this.token) h['Authorization'] = `Bearer ${this.token}`;
    return h;
  }

  private async get<T>(endpoint: string): Promise<ApiResponse<T>> {
    if (!this.enabled) return { success: false, error: 'runtime offline' };
    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, { headers: this.headers() });
      return await response.json();
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : 'Unknown error' };
    }
  }

  private async send<T>(
    method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
    endpoint: string,
    data?: unknown,
  ): Promise<ApiResponse<T>> {
    if (!this.enabled) return { success: false, error: 'runtime offline' };
    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        method,
        headers: this.headers(!!data),
        body: data ? JSON.stringify(data) : undefined,
      });
      return await response.json();
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : 'Unknown error' };
    }
  }

  // ── lifecycle (CONTRACTS 1.1) ──────────────────────────────────
  healthCheck(): Promise<ApiResponse> {
    return this.get('/health');
  }
  shutdown(): Promise<ApiResponse> {
    return this.send('POST', '/shutdown');
  }

  // ── sessions (CONTRACTS 1.3) ───────────────────────────────────
  createSession(): Promise<ApiResponse<{ id: string }>> {
    return this.send('POST', '/sessions');
  }
  listSessions(): Promise<ApiResponse> {
    return this.get('/sessions');
  }
  getSession(id: string): Promise<ApiResponse> {
    return this.get(`/sessions/${id}`);
  }
  /** paginated history load */
  getMessages(id: string, cursor?: string): Promise<ApiResponse> {
    return this.get(`/sessions/${id}/messages${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
  }
  abort(id: string): Promise<ApiResponse> {
    return this.send('POST', `/sessions/${id}/abort`);
  }
  regenerate(id: string, messageId: string): Promise<ApiResponse> {
    return this.send('POST', `/sessions/${id}/messages/${messageId}/regenerate`);
  }
  exportSession(id: string, format: 'md' | 'json' = 'md'): string {
    return `${this.baseUrl}/sessions/${id}/export?format=${format}`;
  }

  // Note: streaming a new message is POST /sessions/{id}/messages → SSE.
  // EventSource can't POST, so that path lives in lib/sse.ts (streamMessages).

  // ── HITL approvals (CONTRACTS 1.2) ─────────────────────────────
  /** respond to a tool_approval_required control event */
  respondToolApproval(sessionId: string, decision: ToolApprovalDecision): Promise<ApiResponse> {
    return this.send('POST', `/sessions/${sessionId}/tool-approval`, decision);
  }
  /** recover prompts still parked after an SSE reconnect (CONTRACTS 1.2) */
  getPendingApprovals(sessionId: string): Promise<ApiResponse<PendingApproval[]>> {
    return this.get<PendingApproval[]>(`/sessions/${sessionId}/pending-approvals`);
  }

  // ── skills + learning loop (CONTRACTS 1.3, LEARNING_LOOP 5.2) ───
  getSkills(): Promise<ApiResponse> {
    return this.get('/skills');
  }
  getSkill(id: string): Promise<ApiResponse> {
    return this.get(`/skills/${id}`);
  }
  /** manual "스킬 정리" — curator transition trigger */
  curateSkills(): Promise<ApiResponse> {
    return this.send('POST', '/skills/curate');
  }
  setSkillState(id: string, state: 'active' | 'stale' | 'archived'): Promise<ApiResponse> {
    return this.send('PATCH', `/skills/${id}/state`, { state });
  }
  /** respond to a skill_patch_proposed control event (approve/reject/edited) */
  respondSkillPatch(id: string, decision: SkillPatchDecision): Promise<ApiResponse> {
    return this.send('POST', `/skills/${id}/patch`, decision);
  }
  /** version history (diff · rollback UI) */
  getSkillHistory(id: string): Promise<ApiResponse> {
    return this.get(`/skills/${id}/history`);
  }
  rollbackSkill(id: string, version: number): Promise<ApiResponse> {
    return this.send('POST', `/skills/${id}/rollback`, { version });
  }

  // ── plugins (CONTRACTS 1.3) ────────────────────────────────────
  getPlugins(): Promise<ApiResponse<Plugin[]>> {
    return this.get<Plugin[]>('/plugins');
  }
  setPluginEnabled(id: string, enabled: boolean): Promise<ApiResponse> {
    return this.send('PATCH', `/plugins/${id}/enabled`, { enabled });
  }
  deletePlugin(id: string): Promise<ApiResponse> {
    return this.send('DELETE', `/plugins/${id}`);
  }

  // ── connectors (CONTRACTS 4) ───────────────────────────────────
  getConnectors(): Promise<ApiResponse> {
    return this.get('/connectors');
  }
  getConnector(id: string): Promise<ApiResponse> {
    return this.get(`/connectors/${id}`);
  }
  /** start OAuth for a new connector (returns an auth URL the app opens) */
  connectConnector(connectorType: string): Promise<ApiResponse<{ oauth_url: string }>> {
    return this.send('POST', '/connectors', { connector_type: connectorType });
  }
  deleteConnector(id: string): Promise<ApiResponse> {
    return this.send('DELETE', `/connectors/${id}`);
  }
  getConnectorStatus(id: string): Promise<ApiResponse> {
    return this.get(`/connectors/${id}/status`);
  }

  // ── personalization / config (CONTRACTS 1.3) ───────────────────
  getMemories(): Promise<ApiResponse> {
    return this.get('/memories');
  }
  getModels(): Promise<ApiResponse> {
    return this.get('/models');
  }
  getSettings(): Promise<ApiResponse> {
    return this.get('/settings');
  }
  updateSettings(settings: unknown): Promise<ApiResponse> {
    return this.send('PUT', '/settings', settings);
  }

  // ── artifacts / market install (CONTRACTS 1.3, 2.3) ────────────
  installArtifact(payload: unknown): Promise<ApiResponse> {
    return this.send('POST', '/artifacts/install', payload);
  }
  removeArtifact(id: string): Promise<ApiResponse> {
    return this.send('DELETE', `/artifacts/${id}`);
  }

  // ── Learning Impact (eval) — MVP reads a static fixture (EVAL 6.4) ──
  // Runtime exposes eval/results/latest.json as a static file; no endpoint
  // required for the demo. (Future: GET /eval/runs — EVAL 6.4.)
  getImpactReport(): Promise<ApiResponse<ImpactReport>> {
    return this.get<ImpactReport>('/eval/results/latest.json');
  }
}

// Export singleton instance
export const api = new ApiClient();
export default api;

// Made with Bob
