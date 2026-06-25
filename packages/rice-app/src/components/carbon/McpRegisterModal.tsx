import React, { useEffect, useMemo, useState } from 'react';
import { Button, Icon, Modal } from '../ui';
import type { McpTransport } from '../../lib/learningData';
import { useToast } from '../../lib/toast/ToastContext';

/**
 * McpRegisterModal — 로컬 MCP 서버 등록 모달 (개발자).
 *
 * ModelRegisterModal과 동일한 게이트형 흐름을 따른다. 다만 대상은 API 키가 아니라
 * "내 기기에서 실행/연결되는 MCP 서버"이므로, 임의 로컬 코드 실행에 대한 경고를
 * 명시하고 전송 방식(stdio / http)을 분기한다.
 *
 * 등록 흐름(게이트형):
 *   1. 전송 방식 선택 → 이름 · 실행 명령(stdio) 또는 엔드포인트(http) 입력
 *   2. "연결 테스트"로 핸드셰이크 검증 (선택이 아니라 필수 게이트)
 *   3. 테스트 통과(= 노출 도구 확인) 시에만 "등록" 활성화
 *   4. 등록 시 즉시 목록에 추가 후 닫힘
 */

export interface McpRegisterPayload {
  name: string;
  transport: McpTransport;
  /** 테스트에서 발견한 노출 도구 수 (프로토타입: 목 값) */
  toolCount: number;
}

type TransportType = 'stdio' | 'http';
type TestState = 'idle' | 'testing' | 'ok' | 'fail';

export interface McpRegisterModalProps {
  open: boolean;
  onClose: () => void;
  /** 테스트 통과 후 등록 확정 시 호출 */
  onRegister: (payload: McpRegisterPayload) => void;
}

/** "KEY=VALUE" 줄들을 env 배열로 파싱 (빈 줄·형식 불량 줄은 무시) */
function parseEnv(raw: string): { key: string; value: string }[] {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && line.includes('='))
    .map((line) => {
      const idx = line.indexOf('=');
      return { key: line.slice(0, idx).trim(), value: line.slice(idx + 1).trim() };
    })
    .filter((e) => e.key.length > 0);
}

const inputCls =
  'w-full h-10 px-3.5 bg-field-01 text-text-primary font-mono text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder placeholder:font-sans';

export const McpRegisterModal: React.FC<McpRegisterModalProps> = ({ open, onClose, onRegister }) => {
  const [transport, setTransport] = useState<TransportType>('stdio');
  const [name, setName] = useState('');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('');
  const [env, setEnv] = useState('');
  const [endpoint, setEndpoint] = useState('');
  const [test, setTest] = useState<TestState>('idle');
  const [toolCount, setToolCount] = useState(0);
  const toast = useToast();

  // 모달이 열릴 때마다 폼 초기화
  useEffect(() => {
    if (open) {
      setTransport('stdio');
      setName('');
      setCommand('');
      setArgs('');
      setEnv('');
      setEndpoint('');
      setTest('idle');
      setToolCount(0);
    }
  }, [open]);

  const trimmedName = name.trim();
  const fieldsReady = useMemo(() => {
    if (trimmedName.length === 0) return false;
    return transport === 'stdio' ? command.trim().length > 0 : endpoint.trim().length > 0;
  }, [trimmedName, transport, command, endpoint]);

  const canTest = fieldsReady && test !== 'testing';
  const canRegister = test === 'ok';

  // 입력이 바뀌면 이전 테스트 결과 무효
  const invalidate = () => setTest((t) => (t === 'idle' ? t : 'idle'));

  const handleTransport = (t: TransportType) => {
    setTransport(t);
    invalidate();
  };

  const runTest = () => {
    if (!canTest) return;
    setTest('testing');
    // 진척률 없는 핸드셰이크 → indeterminate 진행률 토스트(progress 생략).
    const sublabel = transport === 'stdio' ? command.trim() : endpoint.trim();
    const job = toast.progress({ label: '연결 확인 중', sublabel: sublabel || trimmedName });
    // 프로토타입: 실제 핸드셰이크 없이 형식만 검증
    window.setTimeout(() => {
      const ok =
        transport === 'stdio'
          ? command.trim().length > 0
          : /^https?:\/\/.+/i.test(endpoint.trim());
      if (ok) {
        // 발견 도구 수는 데모용으로 입력에서 의사난수 생성
        const found = 3 + ((command.length + endpoint.length) % 5);
        setToolCount(found);
        setTest('ok');
        job.done('success', `연결 성공 · 도구 ${found}개`);
      } else {
        setTest('fail');
        job.done('error', '연결 실패');
      }
    }, 1000);
  };

  const buildTransport = (): McpTransport =>
    transport === 'stdio'
      ? {
          type: 'stdio',
          command: command.trim(),
          args: args
            .trim()
            .split(/\s+/)
            .filter((a) => a.length > 0),
          env: parseEnv(env).length > 0 ? parseEnv(env) : undefined,
        }
      : { type: 'http', endpoint: endpoint.trim() };

  const handleRegister = () => {
    if (!canRegister) return;
    onRegister({ name: trimmedName, transport: buildTransport(), toolCount });
  };

  const footer = (
    <div className="flex items-center justify-between w-full">
      <Button kind="ghost" size="sm" onClick={onClose} disabled={test === 'testing'} className="!h-7">
        취소
      </Button>
      <div className="flex items-center gap-2">
        <Button
          kind="tertiary"
          size="sm"
          onClick={runTest}
          loading={test === 'testing'}
          disabled={!canTest}
          icon={test !== 'testing' ? <Icon name="connect" size={14} /> : undefined}
          className="!h-7"
        >
          {test === 'testing' ? '테스트 중…' : test === 'ok' ? '다시 테스트' : '연결 테스트'}
        </Button>
        {/* 등록은 테스트 통과(게이트) 후에만 활성화 — 비활성 시 hover 안내 */}
        <div className="relative group">
          <Button kind="primary" size="sm" onClick={handleRegister} disabled={!canRegister} className="!h-7">
            <Icon name="check" size={14} className="mr-1.5" />
            등록
          </Button>
          {!canRegister && (
            <div className="pointer-events-none absolute bottom-full right-0 mb-2 w-max max-w-[240px] px-2.5 py-1.5 rounded-md bg-text-primary text-text-inverse text-caption-01 shadow-overlay opacity-0 translate-y-1 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-150">
              {fieldsReady
                ? "먼저 '연결 테스트'를 통과하세요"
                : transport === 'stdio'
                ? '이름과 실행 명령을 입력한 뒤 연결 테스트를 실행하세요'
                : '이름과 엔드포인트를 입력한 뒤 연결 테스트를 실행하세요'}
              <span className="absolute top-full right-4 -mt-px border-4 border-transparent border-t-text-primary" />
            </div>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <Modal
      open={open}
      onClose={() => {
        if (test !== 'testing') onClose();
      }}
      title="로컬 MCP 서버 등록"
      size="sm"
      footer={footer}
    >
      <div className="space-y-5">
        {/* 실행 경고 — 로컬 MCP의 핵심 신뢰 모델 */}
        <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-[#da1e28]/10 border border-[#da1e28]/30">
          <Icon name="warning" size={15} filled className="text-[#ff8389] flex-shrink-0 mt-0.5" />
          <p className="text-caption-01 text-text-helper leading-relaxed">
            로컬 MCP 서버는 <span className="text-[#ff8389] font-medium">내 컴퓨터에서 직접 코드를 실행</span>합니다.
            신뢰할 수 있는 명령·엔드포인트만 등록하세요.
          </p>
        </div>

        {/* 전송 방식 */}
        <div>
          <label className="text-label-01 text-text-secondary mb-2 block">전송 방식</label>
          <div className="grid grid-cols-2 gap-1.5">
            {([
              { id: 'stdio' as const, label: 'stdio', sub: '로컬 명령 실행' },
              { id: 'http' as const, label: 'HTTP', sub: '로컬 엔드포인트' },
            ]).map((opt) => {
              const active = opt.id === transport;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => handleTransport(opt.id)}
                  aria-pressed={active}
                  className={`px-3 py-2 rounded-lg border text-left transition-all duration-150 ${
                    active
                      ? 'border-interactive bg-[#0f62fe]/10 text-text-primary'
                      : 'border-border-subtle-01 bg-background text-text-secondary hover:bg-layer-hover-01 hover:border-border-strong-01'
                  }`}
                >
                  <div className="text-body-short-01 font-semibold">{opt.label}</div>
                  <div className="text-caption-01 text-text-helper">{opt.sub}</div>
                </button>
              );
            })}
          </div>
        </div>

        {/* 이름 */}
        <div>
          <label className="text-label-01 text-text-secondary mb-1.5 block">이름</label>
          <input
            type="text"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              invalidate();
            }}
            placeholder="예: Filesystem, Git"
            className={inputCls.replace('font-mono ', '')}
          />
        </div>

        {transport === 'stdio' ? (
          <>
            {/* 실행 명령 */}
            <div>
              <label className="text-label-01 text-text-secondary mb-1.5 block">실행 명령</label>
              <input
                type="text"
                value={command}
                onChange={(e) => {
                  setCommand(e.target.value);
                  invalidate();
                }}
                placeholder="npx · uvx · python …"
                autoComplete="off"
                spellCheck={false}
                className={inputCls}
              />
            </div>
            {/* 인자 */}
            <div>
              <label className="text-label-01 text-text-secondary mb-1.5 block">
                인자 <span className="text-text-placeholder font-normal">· 공백으로 구분</span>
              </label>
              <input
                type="text"
                value={args}
                onChange={(e) => {
                  setArgs(e.target.value);
                  invalidate();
                }}
                placeholder="-y @modelcontextprotocol/server-filesystem /path"
                autoComplete="off"
                spellCheck={false}
                className={inputCls}
              />
            </div>
            {/* 환경변수 */}
            <div>
              <label className="text-label-01 text-text-secondary mb-1.5 block">
                환경변수 <span className="text-text-placeholder font-normal">· 선택 · KEY=VALUE 줄바꿈</span>
              </label>
              <textarea
                value={env}
                onChange={(e) => {
                  setEnv(e.target.value);
                  invalidate();
                }}
                placeholder={'API_TOKEN=...\nLOG_LEVEL=info'}
                rows={2}
                spellCheck={false}
                className={`${inputCls} h-auto py-2.5 resize-none leading-relaxed`}
              />
            </div>
          </>
        ) : (
          /* HTTP 엔드포인트 */
          <div>
            <label className="text-label-01 text-text-secondary mb-1.5 block">엔드포인트</label>
            <input
              type="text"
              value={endpoint}
              onChange={(e) => {
                setEndpoint(e.target.value);
                invalidate();
              }}
              placeholder="http://localhost:3000/mcp"
              autoComplete="off"
              spellCheck={false}
              className={inputCls}
            />
          </div>
        )}

        {/* 테스트 결과 인라인 표시 */}
        {test === 'testing' && (
          <div className="flex items-center gap-2 text-caption-01 text-text-secondary">
            <span className="ia-spinner w-3.5 h-3.5" /> 서버에 연결해 도구를 확인하는 중입니다…
          </div>
        )}
        {test === 'ok' && (
          <div className="flex items-center gap-1.5 text-caption-01 text-support-success">
            <Icon name="check-circle" size={14} /> 연결됨 · 도구 {toolCount}개 발견 · 이제 등록할 수 있습니다
          </div>
        )}
        {test === 'fail' && (
          <div className="flex items-center gap-1.5 text-caption-01 text-support-error">
            <Icon name="warning" size={14} filled />
            {transport === 'stdio'
              ? '실행에 실패했습니다 · 명령을 확인하세요'
              : '엔드포인트에 연결할 수 없습니다 · 주소를 확인하세요 (http/https)'}
          </div>
        )}

        {/* 저장 위치 안내 */}
        <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-layer-02 border border-border-subtle-01">
          <Icon name="lock" size={15} className="text-icon-secondary flex-shrink-0 mt-0.5" />
          <p className="text-caption-01 text-text-helper leading-relaxed">
            서버 설정은 <span className="text-text-secondary font-medium">이 기기에만 저장</span>됩니다.
            <br />
            <span className="text-text-placeholder">현재는 프로토타입 단계로 브라우저 로컬 저장소에 보관됩니다.</span>
          </p>
        </div>
      </div>
    </Modal>
  );
};

// Made with Bob
