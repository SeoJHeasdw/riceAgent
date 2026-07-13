import React, { useEffect, useRef, useState } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import { Button, Icon, Modal } from '../ui';
import type { LocalMcpTransportPayload } from '../../lib/api';

/**
 * McpRegisterModal — 로컬 MCP 서버 등록 모달 (개발자).
 *
 * 실제 MCP 서버는 거의 항상 README 에 `{"mcpServers": {"name": {...}}}` 형태의 JSON
 * 설치 스니펫을 제공한다 — 그래서 이 모달은 필드별 폼이 아니라 **JSON 붙여넣기/파일
 * 업로드**를 1차 입력으로 삼는다(개별 command/args/env 필드 입력은 안 함).
 *
 * mcpServers 래퍼에 서버가 여러 개 있으면(예: 기존 .mcp.json 통째로 붙여넣기) 전부
 * 파싱해서 목록으로 보여주고, 체크박스로 고른 것만 테스트·등록한다.
 *
 * 흐름(둘 다 실제 백엔드 호출 — 부작용 경계가 다르다):
 *   1. JSON 붙여넣기(또는 파일 선택/드래그) → 텍스트 영역 밖을 클릭(blur)하면 자동 파싱
 *      → 서버 목록(이름 편집 가능) 표시
 *   2. "연결 테스트" → 선택된 항목을 실제로 spawn(stdio)/probe(http) 해서 tools/list 를
 *      받는다. **부작용 없음** — `.mcp.json` 은 안 건드린다. 등록의 필수 게이트.
 *   3. 테스트 통과한 항목만 "등록" 대상이 된다 — 발견한 도구 개수를 보여준다.
 *   4. "등록" → 통과한 항목을 `.mcp.json` 에 실제로 쓰고 바로 warm 한다.
 */

export interface McpRegisterModalProps {
  open: boolean;
  onClose: () => void;
  /** 부작용 없음(spawn/probe 만) — null 이면 요청 자체가 실패한 것 */
  onTest: (
    payload: LocalMcpTransportPayload,
  ) => Promise<{ ok: boolean; error: string | null; tools: string[] } | null>;
  /** 여기서부터 부작용(.mcp.json 에 실제로 씀 + warm). true=성공 */
  onRegister: (name: string, payload: LocalMcpTransportPayload) => Promise<boolean>;
}

interface ParsedEntry {
  name: string;
  payload: LocalMcpTransportPayload;
}

type RowTestStatus = 'idle' | 'testing' | 'ok' | 'fail';

/** 파싱 에러 코드 — 렌더 시점에 t()로 변환한다. */
type ParseErrorCode = 'parseErrorInvalidJson' | 'parseErrorNotObject' | 'parseErrorNoServer';

const PARSE_ERROR_KEYS = {
  parseErrorInvalidJson: 'mcpRegister.parseErrorInvalidJson',
  parseErrorNotObject: 'mcpRegister.parseErrorNotObject',
  parseErrorNoServer: 'mcpRegister.parseErrorNoServer',
} as const satisfies Record<ParseErrorCode, `mcpRegister.${string}`>;

interface RowState {
  entry: ParsedEntry;
  selected: boolean;
  test: RowTestStatus;
  testErrorCode: 'testFail' | null;
  testErrorRaw: string | null;
  tools: string[];
  registered: boolean;
  registerErrorCode: 'registerFail' | null;
}

const EXAMPLE_JSON = `{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
    }
  }
}`;

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function toPayload(cfg: Record<string, unknown>): LocalMcpTransportPayload | null {
  if (typeof cfg.command === 'string' && cfg.command.trim()) {
    return {
      transport: 'stdio',
      command: cfg.command,
      args: Array.isArray(cfg.args) ? cfg.args.map((a) => String(a)) : [],
      env: isPlainObject(cfg.env) ? (cfg.env as Record<string, string>) : {},
    };
  }
  if (typeof cfg.url === 'string' && cfg.url.trim()) {
    return {
      transport: 'http',
      url: cfg.url,
      headers: isPlainObject(cfg.headers) ? (cfg.headers as Record<string, string>) : {},
    };
  }
  return null;
}

/** README 스니펫 그대로(`{"mcpServers": {...}}`), 래퍼 없는 다중({"name": {...}, ...}),
 * 래퍼도 이름도 없는 단일({"command": ...}) 셋 다 받는다. */
function parseLocalMcpJson(text: string): { entries: ParsedEntry[] } | { errorCode: ParseErrorCode } {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { errorCode: 'parseErrorInvalidJson' };
  }
  if (!isPlainObject(data)) return { errorCode: 'parseErrorNotObject' };

  const container = isPlainObject(data.mcpServers) ? (data.mcpServers as Record<string, unknown>) : data;
  const entries: ParsedEntry[] = [];
  for (const [name, cfg] of Object.entries(container)) {
    if (!isPlainObject(cfg)) continue;
    const payload = toPayload(cfg);
    if (payload) entries.push({ name, payload });
  }
  if (entries.length > 0) return { entries };

  const bare = toPayload(data);
  if (bare) return { entries: [{ name: '', payload: bare }] };

  return { errorCode: 'parseErrorNoServer' };
}

const textareaCls =
  'w-full h-40 px-3.5 py-2.5 bg-field-01 text-text-primary font-mono text-caption-01 leading-relaxed border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder resize-none disabled:opacity-60';

/** SectionStep — "1", "2" 같은 원형 배지로 입력 → 서버 목록 → 등록의 흐름을 시각적으로 안내한다.
 * noMargin: 부모(예: 버튼과 나란한 flex row)가 이미 간격을 잡아줄 때 자체 mb를 뺀다. */
const SectionStep: React.FC<{ n: number; children: React.ReactNode; noMargin?: boolean }> = ({
  n,
  children,
  noMargin,
}) => (
  <div className={`flex items-center gap-2 ${noMargin ? '' : 'mb-1.5'}`}>
    <span className="flex-shrink-0 inline-flex items-center justify-center w-5 h-5 rounded-full bg-interactive text-text-on-color text-label-micro font-semibold">
      {n}
    </span>
    <label className="text-label-01 text-text-secondary">{children}</label>
  </div>
);

export const McpRegisterModal: React.FC<McpRegisterModalProps> = ({ open, onClose, onTest, onRegister }) => {
  const { t } = useTranslation('connectors');
  const [jsonText, setJsonText] = useState('');
  const [parseErrorCode, setParseErrorCode] = useState<ParseErrorCode | null>(null);
  const [rows, setRows] = useState<RowState[]>([]);
  const [testingAll, setTestingAll] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [registerSummaryKey, setRegisterSummaryKey] = useState<
    | null
    | { key: 'mcpRegister.registerSummaryAllFail' }
    | { key: 'mcpRegister.registerSummaryPartial'; success: number; total: number }
  >(null);
  const [dragActive, setDragActive] = useState(false);
  const [lastParsedText, setLastParsedText] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const busy = testingAll || registering;

  useEffect(() => {
    if (open) {
      setJsonText('');
      setParseErrorCode(null);
      setRows([]);
      setTestingAll(false);
      setRegistering(false);
      setRegisterSummaryKey(null);
      setDragActive(false);
      setLastParsedText('');
    }
  }, [open]);

  const applyParse = (text: string) => {
    setLastParsedText(text);
    const result = parseLocalMcpJson(text);
    if ('errorCode' in result) {
      setParseErrorCode(result.errorCode);
      setRows([]);
      return;
    }
    setParseErrorCode(null);
    setRegisterSummaryKey(null);
    setRows(
      result.entries.map((entry) => ({
        entry,
        selected: entry.name.trim().length > 0,
        test: 'idle',
        testErrorCode: null,
        testErrorRaw: null,
        tools: [],
        registered: false,
        registerErrorCode: null,
      })),
    );
  };

  const loadJsonFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? '');
      setJsonText(text);
      applyParse(text);
    };
    reader.readAsText(file);
  };

  const handleFileChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // 같은 파일을 다시 골라도 onChange 가 발생하도록
    if (file) loadJsonFile(file);
  };

  // 텍스트 영역에 .mcp.json/README 스니펫 파일을 바로 끌어다 놓을 수 있게 — "파일 선택"을
  // 못 보고 지나쳐도 흔한 동작(파일 드래그)으로 등록을 시작할 수 있게 하는 보조 경로.
  const handleDragOver = (e: React.DragEvent) => {
    if (busy) return;
    e.preventDefault();
    setDragActive(true);
  };
  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
  };
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    if (busy) return;
    const file = e.dataTransfer.files?.[0];
    if (file) loadJsonFile(file);
  };

  const setRowName = (idx: number, name: string) => {
    setRows((rs) =>
      rs.map((r, i) => (i === idx ? { ...r, entry: { ...r.entry, name }, test: 'idle', testErrorCode: null, testErrorRaw: null } : r)),
    );
  };
  const toggleRowSelected = (idx: number) => {
    setRows((rs) => rs.map((r, i) => (i === idx ? { ...r, selected: !r.selected } : r)));
  };

  const testableRows = rows.filter((r) => r.selected && r.entry.name.trim().length > 0);
  const canTest = testableRows.length > 0 && !busy;
  const canRegister = rows.some((r) => r.selected && r.test === 'ok' && !r.registered) && !busy;

  const runTestAll = async () => {
    if (!canTest) return;
    setTestingAll(true);
    for (let i = 0; i < rows.length; i++) {
      if (!rows[i].selected || !rows[i].entry.name.trim()) continue;
      setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, test: 'testing', testErrorCode: null, testErrorRaw: null } : r)));
      const result = await onTest(rows[i].entry.payload);
      setRows((rs) =>
        rs.map((r, idx) => {
          if (idx !== i) return r;
          if (result?.ok) return { ...r, test: 'ok', tools: result.tools, testErrorCode: null, testErrorRaw: null };
          // 백엔드가 아직 code를 내려주지 않는 경우 raw error를 별도 보관하되 UI 기본 문구는 카탈로그 키를 쓴다
          return { ...r, test: 'fail', testErrorCode: 'testFail', testErrorRaw: result?.error ?? null };
        }),
      );
    }
    setTestingAll(false);
  };

  const runRegisterSelected = async () => {
    if (!canRegister) return;
    setRegistering(true);
    setRegisterSummaryKey(null);
    const targets = rows
      .map((r, i) => ({ r, i }))
      .filter(({ r }) => r.selected && r.test === 'ok' && !r.registered);
    let successCount = 0;
    for (const { r, i } of targets) {
      const ok = await onRegister(r.entry.name.trim(), r.entry.payload);
      if (ok) {
        successCount += 1;
        setRows((rs) => rs.map((row, idx) => (idx === i ? { ...row, registered: true, registerErrorCode: null } : row)));
      } else {
        setRows((rs) =>
          rs.map((row, idx) =>
            idx === i ? { ...row, registerErrorCode: 'registerFail' } : row,
          ),
        );
      }
    }
    setRegistering(false);
    if (successCount === targets.length) {
      onClose();
    } else if (successCount === 0) {
      setRegisterSummaryKey({ key: 'mcpRegister.registerSummaryAllFail' });
    } else {
      setRegisterSummaryKey({ key: 'mcpRegister.registerSummaryPartial', success: successCount, total: targets.length });
    }
  };

  const footer = (
    <div className="flex items-center justify-between w-full">
      <Button kind="ghost" size="xs" onClick={() => { if (!busy) onClose(); }} disabled={busy}>
        {t('mcpRegister.cancel')}
      </Button>
      <div className="flex items-center gap-2">
        <Button
          kind="tertiary"
          size="xs"
          onClick={runTestAll}
          loading={testingAll}
          disabled={!canTest}
          icon={!testingAll ? <Icon name="connect" size={12} /> : undefined}
        >
          {testingAll ? t('mcpRegister.btnTesting') : t('mcpRegister.btnTest')}
        </Button>
        <div className="relative group">
          <Button kind="primary" size="xs" onClick={runRegisterSelected} loading={registering} disabled={!canRegister}>
            <Icon name="check" size={12} className="mr-1" />
            {t('mcpRegister.btnRegister')}
          </Button>
          {!canRegister && !registering && (
            <div className="pointer-events-none absolute bottom-full right-0 mb-2 w-max max-w-[240px] px-2.5 py-1.5 rounded-md bg-text-primary text-text-inverse text-caption-01 shadow-elevation-2 opacity-0 translate-y-1 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-150">
              {rows.length === 0 ? t('mcpRegister.tooltipPaste') : t('mcpRegister.tooltipNeedTest')}
              <span className="absolute top-full right-4 -mt-px border-4 border-transparent border-t-text-primary" />
            </div>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <Modal open={open} onClose={() => { if (!busy) onClose(); }} title={t('mcpRegister.modalTitle')} size="md" footer={footer} compactFooter>
      <div className="space-y-4">
        {/* 실행 경고 — 로컬 MCP의 핵심 신뢰 모델 */}
        <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-red-60/10 border border-red-60/30">
          <Icon name="warning" size={15} filled className="text-red-40 flex-shrink-0 mt-0.5" />
          <p className="text-caption-01 text-text-helper leading-relaxed">
            {t('detail.localWarning_pre')}
            <span className="text-red-40 font-medium">{t('detail.localWarning_strong')}</span>
            {t('detail.localWarning_post')}
          </p>
        </div>

        {/* JSON 입력 */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <SectionStep n={1} noMargin>{t('mcpRegister.sectionInputLabel')}</SectionStep>
            <Button
              kind="secondary"
              size="sm"
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
              icon={<Icon name="attachment" size={14} />}
            >
              {t('mcpRegister.importFromFile')}
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              onChange={handleFileChosen}
              className="hidden"
            />
          </div>
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className="relative"
          >
            <textarea
              value={jsonText}
              disabled={busy}
              onChange={(e) => {
                setJsonText(e.target.value);
                setParseErrorCode(null);
              }}
              onBlur={() => {
                // 붙여넣고 다른 곳을 클릭하면 자동으로 파싱 — 파일 업로드 경로와 동작을 맞춰서
                // "확인" 버튼을 눌러야 하는 애매한 중간 단계를 없앤다. 이미 파싱한 텍스트와
                // 같으면 건너뛰어 테스트/등록 진행 상태를 지우지 않는다.
                if (busy || !jsonText.trim() || jsonText === lastParsedText) return;
                applyParse(jsonText);
              }}
              placeholder={EXAMPLE_JSON}
              spellCheck={false}
              className={`${textareaCls} ${dragActive ? 'border-focus ring-1 ring-focus' : ''}`}
            />
            {dragActive && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-lg bg-layer-01/90 border-2 border-dashed border-focus">
                <span className="inline-flex items-center gap-1.5 text-body-short-01 font-medium text-link-primary">
                  <Icon name="document" size={16} /> {t('mcpRegister.dragDropLabel')}
                </span>
              </div>
            )}
          </div>
          <p className="mt-1.5 text-caption-01 text-text-helper leading-relaxed">
            {t('mcpRegister.inputHint')}
          </p>
          {parseErrorCode && (
            <div className="mt-2 flex items-center gap-1.5 text-caption-01 text-support-error">
              <Icon name="warning" size={14} filled />
              {t(PARSE_ERROR_KEYS[parseErrorCode])}
            </div>
          )}
        </div>

        {/* 파싱된 서버 목록 */}
        {rows.length > 0 && (
          <div>
            <SectionStep n={2}>{t('mcpRegister.serverListTitle', { count: rows.length })}</SectionStep>
            <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
              {rows.map((row, idx) => (
                <div key={idx} className="bg-background px-3 py-2.5">
                  <div className="flex items-center gap-2.5">
                    <input
                      type="checkbox"
                      checked={row.selected}
                      disabled={busy}
                      onChange={() => toggleRowSelected(idx)}
                      className="w-4 h-4 flex-shrink-0 accent-interactive"
                    />
                    <input
                      type="text"
                      value={row.entry.name}
                      disabled={busy}
                      onChange={(e) => setRowName(idx, e.target.value)}
                      placeholder={t('mcpRegister.namePlaceholder')}
                      className="flex-1 min-w-0 h-8 px-2.5 bg-field-01 text-text-primary text-body-short-01 border border-border-strong-01 rounded-md outline-none focus:border-focus focus:ring-1 focus:ring-focus disabled:opacity-60"
                    />
                    <span className="flex-shrink-0 text-caption-01 font-mono text-text-helper px-1.5 py-0.5 rounded bg-layer-02">
                      {row.entry.payload.transport}
                    </span>
                    <span className="flex-shrink-0 w-5 flex items-center justify-center">
                      {row.test === 'testing' && <span className="ia-spinner w-3.5 h-3.5" />}
                      {row.test === 'ok' && <Icon name="check-circle" size={16} className="text-support-success" />}
                      {row.test === 'fail' && <Icon name="warning" size={16} filled className="text-support-error" />}
                    </span>
                  </div>
                  <div className="mt-1 pl-[26px] text-caption-01 text-text-helper font-mono truncate">
                    {row.entry.payload.transport === 'stdio'
                      ? [row.entry.payload.command, ...(row.entry.payload.args ?? [])].join(' ')
                      : row.entry.payload.url}
                  </div>
                  {row.test === 'ok' && (
                    <div className="mt-1 pl-[26px] text-caption-01 text-support-success">
                      {t('mcpRegister.testOk', { count: row.tools.length })}
                    </div>
                  )}
                  {row.test === 'fail' && row.testErrorCode && (
                    <div className="mt-1 pl-[26px] text-caption-01 text-support-error truncate">
                      {t('mcpRegister.testFail')}
                    </div>
                  )}
                  {row.registered && (
                    <div className="mt-1 pl-[26px] text-caption-01 text-support-success">{t('mcpRegister.registered')}</div>
                  )}
                  {row.registerErrorCode && (
                    <div className="mt-1 pl-[26px] text-caption-01 text-support-error">{t('mcpRegister.registerFail')}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {registerSummaryKey && (
          <div className="flex items-center gap-1.5 text-caption-01 text-support-error">
            <Icon name="warning" size={14} filled />
            {registerSummaryKey.key === 'mcpRegister.registerSummaryAllFail'
              ? t('mcpRegister.registerSummaryAllFail')
              : t('mcpRegister.registerSummaryPartial', {
                  success: registerSummaryKey.success,
                  total: registerSummaryKey.total,
                })}
          </div>
        )}

        {/* 저장 위치 안내 */}
        <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-layer-02 border border-border-subtle-01">
          <Icon name="lock" size={15} className="text-icon-secondary flex-shrink-0 mt-0.5" />
          <p className="text-caption-01 text-text-helper leading-relaxed">
            <Trans
              i18nKey="mcpRegister.storageHint"
              ns="connectors"
              components={{ path: <span className="text-text-secondary font-medium" /> }}
            />
          </p>
        </div>
      </div>
    </Modal>
  );
};

// Made with Bob
