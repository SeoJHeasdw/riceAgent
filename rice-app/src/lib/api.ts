import type {
  ApiResponse,
  Plugin,
  ImpactReport,
  PendingApproval,
  ToolApprovalDecision,
  SkillPatchDecision,
  CurateConsolidateResult,
  PlatformArtifact,
  PlatformFileResponse,
  PlatformPublishPayload,
} from './types';

/** stdio 또는 http 로컬 MCP 서버 정의 — GET /connectors/local/{test,''} 요청 바디 공통 shape. */
export interface LocalMcpTransportPayload {
  transport: 'stdio' | 'http';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
}

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
  /** Subscribers notified when the runtime connects/disconnects. The connect
   *  probe (App bootstrap) is async, so screens can't read `isEnabled` once at
   *  mount — they subscribe here to react when it flips on. */
  private enabledListeners = new Set<(enabled: boolean) => void>();

  constructor(baseUrl: string = 'http://localhost:8080') {
    this.baseUrl = baseUrl;
  }

  setBaseUrl(url: string) {
    this.baseUrl = url;
  }

  setEnabled(v: boolean) {
    const changed = this.enabled !== v;
    this.enabled = v;
    if (changed) this.enabledListeners.forEach((cb) => cb(v));
  }

  /** Subscribe to connect/disconnect; returns an unsubscribe fn. */
  onEnabledChange(cb: (enabled: boolean) => void): () => void {
    this.enabledListeners.add(cb);
    return () => this.enabledListeners.delete(cb);
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
  /** delete a session and its messages (history sidebar) */
  deleteSession(id: string): Promise<ApiResponse> {
    return this.send('DELETE', `/sessions/${id}`);
  }
  /** paginated history load */
  getMessages(id: string, cursor?: string): Promise<ApiResponse> {
    return this.get(`/sessions/${id}/messages${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
  }
  abort(id: string): Promise<ApiResponse> {
    return this.send('POST', `/sessions/${id}/abort`);
  }
  /** Name an untitled session from its opening exchange — called after the first
   *  turn's `done`. Separate from the message stream so a slow title model never
   *  delays the answer; returns the generated title (or null if not titled). */
  generateTitle(id: string): Promise<ApiResponse<{ title: string | null }>> {
    return this.send('POST', `/sessions/${id}/title`);
  }
  /** read a workspace file (builtin file-tool sandbox) — text content */
  readWorkspaceFile(path: string): Promise<ApiResponse> {
    return this.get(`/workspace/file?path=${encodeURIComponent(path)}`);
  }
  /** open a workspace file in the user's OS default app (Excel→office, .txt→editor…) */
  openWorkspaceFile(path: string): Promise<ApiResponse> {
    return this.send('POST', '/workspace/open', { path });
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
  /** manual "스킬 정리" — curator transition trigger (rule-based only) */
  curateSkills(): Promise<ApiResponse> {
    return this.send('POST', '/skills/curate');
  }
  /** Phase B: 규칙 전이 + LLM 통합 제안(있으면) — provider 미배선이면 proposals=[] */
  consolidateSkills(): Promise<ApiResponse<CurateConsolidateResult>> {
    return this.send('POST', '/skills/curate?mode=consolidate');
  }
  setSkillState(id: string, state: 'active' | 'stale' | 'archived'): Promise<ApiResponse> {
    return this.send('PATCH', `/skills/${id}/state`, { state });
  }
  /** respond to a skill_patch_proposed control event (approve/reject/edited) */
  respondSkillPatch(id: string, decision: SkillPatchDecision): Promise<ApiResponse> {
    return this.send('POST', `/skills/${id}/patch`, decision);
  }
  /** Phase B: ArchiveProposal(순수 정리 or 병합 흡수) 승인/거부 — approval_id 없음 */
  answerArchiveDecision(id: string, approved: boolean, absorbedInto?: string | null): Promise<ApiResponse> {
    return this.send('POST', `/skills/${id}/archive-decision`, { approved, absorbed_into: absorbedInto ?? null });
  }
  /** Phase B: 통합 병합 제안(umbrella 생성/patch) 승인/거부 — approval_id 없음,
   *  프론트가 들고 있던 proposed_body를 그대로 반송한다. */
  answerConsolidateDecision(
    id: string,
    approved: boolean,
    proposal?: { proposedBody?: string; description?: string; tags?: string[] },
  ): Promise<ApiResponse<{ version: number | null }>> {
    return this.send('POST', `/skills/${id}/consolidate-decision`, {
      approved,
      proposed_body: proposal?.proposedBody,
      description: proposal?.description ?? '',
      tags: proposal?.tags ?? [],
    });
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
  /** force re-fetch + apply a connector's remote MCP tool catalog (강제 새로고침) */
  resyncConnectorTools(
    id: string,
  ): Promise<ApiResponse<{ count: number; tools: { name: string; requires_approval?: boolean }[] }>> {
    return this.send('POST', `/connectors/${id}/tools/resync`);
  }
  /** detect remote tool-catalog changes without applying (sets a pending update) */
  checkConnectorUpdates(
    id: string,
  ): Promise<ApiResponse<{ changed: boolean; added: string[]; removed: string[] }>> {
    return this.send('POST', `/connectors/${id}/tools/check`);
  }
  /** adopt the pending tool-catalog update (업데이트 적용) */
  applyConnectorToolsUpdate(
    id: string,
  ): Promise<ApiResponse<{ count: number; tools: { name: string; requires_approval?: boolean }[] }>> {
    return this.send('POST', `/connectors/${id}/tools/apply-update`);
  }
  /** persist a tool's call policy — dispatcher enforces it (allow/approval/block) */
  setConnectorToolPermission(
    id: string,
    toolName: string,
    permission: 'allow' | 'approval' | 'block',
  ): Promise<ApiResponse<{ tool_name: string; permission: string }>> {
    return this.send('PUT', `/connectors/${id}/tools/permission`, { tool_name: toolName, permission });
  }
  getConnectorStatus(id: string): Promise<ApiResponse> {
    return this.get(`/connectors/${id}/status`);
  }
  /** `.mcp.json` 에 선언된 로컬 MCP 서버 + 캐시된 도구 목록 (local-MCP 탭 실데이터) */
  getLocalConnectors(): Promise<ApiResponse> {
    return this.get('/connectors/local');
  }
  /** 로컬 MCP 서버를 다시 spawn 해 도구 카탈로그를 갱신한다 (서버 전체 새로고침) */
  refreshLocalConnectors(): Promise<ApiResponse> {
    return this.send('POST', '/connectors/local/refresh');
  }
  /** 등록 전 연결 확인 — 부작용 없음(.mcp.json 안 씀). "등록" 모달의 필수 게이트. */
  testLocalConnector(
    payload: LocalMcpTransportPayload,
  ): Promise<ApiResponse<{ ok: boolean; error: string | null; tool_count: number; tools: string[] }>> {
    return this.send('POST', '/connectors/local/test', payload);
  }
  /** `.mcp.json` 에 서버를 실제로 추가하고 바로 warm 한다(다른 서버는 안 건드림) */
  registerLocalConnector(
    payload: LocalMcpTransportPayload & { name: string },
  ): Promise<ApiResponse> {
    return this.send('POST', '/connectors/local', payload);
  }
  /** `.mcp.json` 에서 서버를 제거한다 */
  deleteLocalConnector(name: string): Promise<ApiResponse> {
    return this.send('DELETE', `/connectors/local/${encodeURIComponent(name)}`);
  }
  /** 임의 MCP 서버 URL 로 OAuth 설정을 자동 발견한다(RFC 9728+8414, 부작용 없음 — 미리보기) */
  discoverCustomConnector(mcpEndpoint: string): Promise<
    ApiResponse<{
      ok: boolean;
      auth_url: string | null;
      token_url: string | null;
      registration_endpoint: string | null;
      scopes: string[];
      issuer: string | null;
    }>
  > {
    return this.send('POST', '/connectors/custom/discover', { mcp_endpoint: mcpEndpoint });
  }
  /** 커스텀 커넥터 등록을 확정한다 — 필요하면 동적 client 등록(RFC 7591) 후 바로 OAuth 시작 */
  addCustomConnector(payload: {
    display_name: string;
    mcp_endpoint: string;
    auth_url: string;
    token_url: string;
    registration_endpoint?: string;
    scopes?: string[];
    client_id?: string;
    client_secret?: string;
  }): Promise<ApiResponse<{ oauth_url: string; connector_id: string }>> {
    return this.send('POST', '/connectors/custom', payload);
  }

  // ── personalization / config (CONTRACTS 1.3) ───────────────────
  getMemories(): Promise<ApiResponse> {
    return this.get('/memories');
  }
  getModels(): Promise<ApiResponse> {
    return this.get('/models');
  }
  getOllamaModels(): Promise<ApiResponse> {
    return this.get('/ollama/models');
  }
  /** 현재 설정된 모델 하나만 설치 여부 확인 (local 모드 전용) */
  checkOllamaModelHealth(): Promise<ApiResponse<{ exists: boolean; model: string }>> {
    return this.get('/ollama/health');
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

// ── Platform API client (rice-platform, localhost:7070) ──────────────────────
/**
 * PlatformApiClient — rice-platform 마켓 서버 클라이언트.
 * 런타임(api)과 완전히 분리된 별도 인스턴스. 인증 없음(데모 단계).
 * platform 오프라인 시에는 메서드가 { success: false }를 반환하고
 * 호출 측(MarketplaceContext)에서 mock 폴백을 유지한다.
 */
class PlatformApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = 'http://localhost:7070') {
    this.baseUrl = baseUrl;
  }

  setBaseUrl(url: string) {
    this.baseUrl = url;
  }

  get base(): string {
    return this.baseUrl;
  }

  private async get<T>(endpoint: string): Promise<ApiResponse<T>> {
    try {
      const res = await fetch(`${this.baseUrl}${endpoint}`);
      return await res.json();
    } catch {
      return { success: false, error: 'platform offline' };
    }
  }

  private async send<T>(
    method: 'POST' | 'PUT' | 'DELETE',
    endpoint: string,
    data?: unknown,
  ): Promise<ApiResponse<T>> {
    try {
      const res = await fetch(`${this.baseUrl}${endpoint}`, {
        method,
        headers: data ? { 'Content-Type': 'application/json' } : {},
        body: data ? JSON.stringify(data) : undefined,
      });
      return await res.json();
    } catch {
      return { success: false, error: 'platform offline' };
    }
  }

  /** 서버 상태 확인 */
  health(): Promise<ApiResponse<{ status: string; version: string }>> {
    return this.get('/health');
  }

  /**
   * GET /catalog — artifact 목록.
   * platform 오프라인 시 { success: false, error: 'platform offline' }.
   */
  getCatalog(params?: {
    type?: 'skill' | 'plugin';
    q?: string;
    category?: string;
    tags?: string[];
    sort?: 'popular' | 'recent' | 'name';
  }): Promise<ApiResponse<PlatformArtifact[]>> {
    const qs = new URLSearchParams();
    if (params?.type) qs.set('type', params.type);
    if (params?.q) qs.set('q', params.q);
    if (params?.category) qs.set('category', params.category);
    params?.tags?.forEach((t) => qs.append('tags', t));
    if (params?.sort) qs.set('sort', params.sort);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return this.get<PlatformArtifact[]>(`/catalog${suffix}`);
  }

  /** GET /artifacts/{id} — 단일 artifact 상세 */
  getArtifact(id: string): Promise<ApiResponse<PlatformArtifact>> {
    return this.get<PlatformArtifact>(`/artifacts/${id}`);
  }

  /**
   * GET /artifacts/{id}/files/{path} — 개별 파일 내용.
   * SkillFileBrowser 에서 파일 내용을 lazy load 할 때 사용.
   */
  getArtifactFile(id: string, filePath: string): Promise<ApiResponse<PlatformFileResponse>> {
    // 경로 구분자(/)는 보존하고 각 세그먼트만 인코딩 — 서버의 {file_path:path} 매칭을 깨지 않으면서
    // 공백·특수문자가 든 파일명도 안전하게 전달한다.
    const encodedPath = filePath.split('/').map(encodeURIComponent).join('/');
    return this.get<PlatformFileResponse>(`/artifacts/${encodeURIComponent(id)}/files/${encodedPath}`);
  }

  /**
   * 모든 skill_files를 한꺼번에 로드해 SkillFile[] 로 반환하는 편의 메서드.
   * 바이너리 파일은 content=null 이므로 자동 필터링.
   */
  async getArtifactFiles(
    artifact: PlatformArtifact,
  ): Promise<import('./types').SkillFile[]> {
    const results = await Promise.all(
      artifact.skill_files.map((p) => this.getArtifactFile(artifact.id, p)),
    );
    return results
      .map((res, i) => {
        if (!res.success || !res.data || res.data.content === null) return null;
        const { path, content, language } = res.data;
        return {
          path: path ?? artifact.skill_files[i],
          language: language as import('./types').SkillFileLanguage,
          content,
        };
      })
      .filter((f): f is import('./types').SkillFile => f !== null);
  }

  /** POST /artifacts/{id}/sponsor — Blue Point 후원 */
  sponsorArtifact(id: string, points: number): Promise<ApiResponse<{ artifact_id: string; total_points: number }>> {
    return this.send('POST', `/artifacts/${id}/sponsor`, { points });
  }

  /**
   * GET /artifacts/{id}/download 의 URL을 반환 — runtime의 POST /artifacts/install 에 전달.
   * 실제 다운로드는 runtime 측에서 수행한다 (CONTRACTS §2.3).
   */
  downloadUrl(id: string): string {
    return `${this.baseUrl}/artifacts/${id}/download`;
  }

  /** POST /artifacts — artifact 게시(publish) */
  publish(payload: PlatformPublishPayload): Promise<ApiResponse<{ id: string; name: string; version: string }>> {
    return this.send('POST', '/artifacts', payload);
  }
}

/** rice-platform 마켓 서버 싱글턴 */
export const platform = new PlatformApiClient();

// Made with Bob
