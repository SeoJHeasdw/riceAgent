import React, { useEffect, useState } from 'react';
import { ModelPicker } from '../ModelPicker';
import { LineIcon } from './primitives';
import { icons } from './iconPaths';
import { WELCOME_CHAT, LOOP_NODES, type MockChatMessage, type LoopNode } from './mockData';
import type { OnboardingStep } from './types';

/**
 * 온보딩 스텝 정의 모음.
 *
 * 각 스텝은 순수한 설정 객체다. 새 튜토리얼을 추가하려면 여기에 OnboardingStep을
 * 하나 더 정의해 buildSteps() 배열에 끼워 넣기만 하면 된다(셸 수정 불필요).
 */

// ── 공용 색상 매핑 ─────────────────────────────────────────────────────────
const ACCENT_CLASSES = {
  blue:   { bg: 'bg-blue-60/15',   text: 'text-blue-50',   ring: 'ring-blue-50',   dot: '#4589ff' },
  teal:   { bg: 'bg-teal-60/15',   text: 'text-teal-60',   ring: 'ring-teal-60',   dot: '#009d9a' },
  purple: { bg: 'bg-purple-60/15', text: 'text-purple-60', ring: 'ring-purple-60', dot: '#8a3ffc' },
  orange: { bg: 'bg-[#ff832b]/10', text: 'text-[#ff832b]', ring: 'ring-[#ff832b]', dot: '#ff832b' },
} as const;

// ── welcomeStep 전용: 채팅 시뮬레이션 ───────────────────────────────────────

const ToolChip: React.FC<{ name: string; label: string }> = ({ name, label }) => (
  <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-layer-02 border border-border-subtle-01 text-caption-01">
    <span className="font-mono text-[#78a9ff] flex-shrink-0">{name}</span>
    <span className="text-text-helper truncate">{label}</span>
    <span className="ia-spinner w-3 h-3 flex-shrink-0 ml-auto" />
  </div>
);

/**
 * 스킬 저장 제안 카드 — 자동 애니메이션 버전.
 *
 * 카드가 마운트되고 1.2초 후 "저장됨" 상태로 전환,
 * 2초 뒤 다시 "저장 전" 상태로 복귀하며 무한 반복.
 * 사용자가 직접 클릭해서도 상태를 전환할 수 있다.
 */
const SkillProposalCard: React.FC<{ skillName: string; ver: string; desc: string; meta: string }> = ({
  skillName, ver, desc, meta,
}) => {
  // true = 저장됨, false = 저장 전
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    // 1.2s 후 저장됨으로, 그 후 2s 후 다시 저장 전으로, 무한 반복
    const SAVE_DELAY   = 1200; // 저장 전 → 저장됨
    const SAVED_HOLD   = 2000; // 저장됨 유지
    let t: ReturnType<typeof setTimeout>;

    const tick = (isSaved: boolean) => {
      t = setTimeout(() => {
        setSaved(!isSaved);
        tick(!isSaved);
      }, isSaved ? SAVED_HOLD : SAVE_DELAY);
    };
    tick(false); // 처음엔 저장 전 상태에서 시작
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="ai-tile px-3 py-2.5 w-full">
      {/* 헤더 */}
      <div className="flex items-center gap-2 mb-1">
        <span className="ai-label"><span className="ai-label__text">AI</span></span>
        <span className="text-[10.5px] font-semibold text-text-primary font-mono">{skillName}</span>
        <span className="text-[10.5px] text-text-helper">{ver}</span>
        <span
          className="ml-auto text-[10.5px] font-semibold flex items-center gap-1 transition-colors duration-300"
          style={{ color: saved ? 'var(--cds-support-success)' : 'var(--cds-text-helper)' }}
        >
          {saved ? '✓ 저장됨' : '제안됨'}
        </span>
      </div>

      {/* 설명 */}
      <p className="text-caption-01 text-text-secondary leading-relaxed mb-2">{desc}</p>

      {/* 액션 영역 — saved 상태에 따라 fade 전환 */}
      <div className="relative h-[22px]">
        {/* 저장 전: 버튼 2개 */}
        <div
          className="absolute inset-0 flex items-center gap-2 transition-opacity duration-300"
          style={{ opacity: saved ? 0 : 1, pointerEvents: saved ? 'none' : 'auto' }}
        >
          <span className="text-[10px] text-text-helper">{meta}</span>
          <div className="flex-1" />
          <button
            onClick={() => setSaved(false)}
            className="text-[11px] text-text-helper px-2 py-0.5 rounded-md"
          >
            이번만 쓰기
          </button>
          <button
            onClick={() => setSaved(true)}
            className="text-[11px] font-semibold text-white bg-interactive px-2.5 py-0.5 rounded-md flex items-center gap-1"
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
              <polyline points="17 21 17 13 7 13 7 21"/>
              <polyline points="7 3 7 8 15 8"/>
            </svg>
            스킬로 저장
          </button>
        </div>

        {/* 저장 후: 성공 텍스트 */}
        <div
          className="absolute inset-0 flex items-center transition-opacity duration-300"
          style={{ opacity: saved ? 1 : 0, pointerEvents: 'none' }}
        >
          <span className="text-caption-01 text-support-success flex items-center gap-1.5">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
              <polyline points="22 4 12 14.01 9 11.01"/>
            </svg>
            스킬로 저장됨 · 다음 세션에서도 사용 가능
          </span>
        </div>
      </div>
    </div>
  );
};

const ChatBubble: React.FC<{ msg: MockChatMessage }> = ({ msg }) => {
  const isUser = msg.role === 'user';
  return (
    <div className={`flex items-end gap-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* 아바타 — agent만 표시, user는 말풍선만 */}
      {!isUser && (
        <div className="w-6 h-6 flex-shrink-0 flex items-center justify-center rounded-md bg-blue-60 text-white text-[10px] font-semibold font-mono self-start mt-0.5">
          R
        </div>
      )}

      <div className={`flex flex-col gap-1.5 ${isUser ? 'items-end max-w-[75%]' : 'items-start w-full'}`}>
        {/* 사용자 말풍선 */}
        {isUser && (
          <div className="bg-interactive text-white text-caption-01 leading-relaxed px-3 py-2 rounded-2xl rounded-br-sm">
            {msg.text}
          </div>
        )}
        {/* Agent 텍스트 */}
        {!isUser && !msg.aiTile && !msg.saveProposal && msg.text && (
          <p className="text-caption-01 leading-relaxed text-text-secondary">
            {msg.text}
          </p>
        )}
        {/* Tool chip */}
        {msg.toolChip && <ToolChip name={msg.toolChip.name} label={msg.toolChip.label} />}
        {/* 스킬 저장 제안 카드 */}
        {msg.saveProposal && (
          <SkillProposalCard
            skillName={msg.saveProposal.skillName}
            ver={msg.saveProposal.ver}
            desc={msg.saveProposal.desc}
            meta={msg.saveProposal.meta}
          />
        )}
      </div>
    </div>
  );
};

/** welcomeStep에 들어가는 채팅 미리보기 패널 */
const WelcomeChatPreview: React.FC = () => (
  <div className="mt-5 rounded-xl border border-border-subtle-01 bg-layer-01 shadow-elevation-2 overflow-hidden">
    {/* 가짜 윈도우 헤더 — macOS 신호등 */}
    <div className="flex items-center gap-1.5 px-3 py-2.5 border-b border-border-subtle-01 bg-layer-02">
      <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
      <span className="w-2.5 h-2.5 rounded-full bg-[#ffbd2e]" />
      <span className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
      <span className="ml-2 text-[10.5px] text-text-helper font-medium">RICE — 새 대화</span>
    </div>

    {/* 메시지 목록 — 모두 즉시 표시 */}
    <div className="px-3 py-3 flex flex-col gap-3">
      {WELCOME_CHAT.map((msg) => (
        <ChatBubble key={msg.id} msg={msg} />
      ))}
    </div>
  </div>
);

// ── conceptStep 전용: Learning Loop 다이어그램 ──────────────────────────────

/** 4개 노드를 원형으로 배치하는 SVG 다이어그램 */
const LoopDiagram: React.FC<{ activeIdx: number }> = ({ activeIdx }) => {
  // 노드 중심 좌표 (100x100 viewBox 기준, 정사각 배치)
  const CX = 50, CY = 50, R = 32;
  const positions = [
    { x: CX,        y: CY - R },       // 상 (대화)
    { x: CX + R,    y: CY },           // 우 (학습)
    { x: CX,        y: CY + R },       // 하 (스킬 생성)
    { x: CX - R,    y: CY },           // 좌 (자기개선)
  ];

  // 화살표 path: node[i] → node[(i+1)%4], 약간 곡선
  const arrowPaths = positions.map((from, i) => {
    const to = positions[(i + 1) % 4];
    // 중심 방향으로 살짝 당긴 제어점
    const mx = (from.x + to.x) / 2 + (CX - (from.x + to.x) / 2) * 0.18;
    const my = (from.y + to.y) / 2 + (CY - (from.y + to.y) / 2) * 0.18;
    return `M ${from.x} ${from.y} Q ${mx} ${my} ${to.x} ${to.y}`;
  });

  return (
    <svg viewBox="0 0 100 100" className="w-full h-full" aria-hidden>
      <defs>
        <marker id="arrowhead" markerWidth="4" markerHeight="4" refX="3" refY="2" orient="auto">
          <path d="M0,0 L0,4 L4,2 Z" fill="var(--cds-border-strong-01)" />
        </marker>
        <marker id="arrowhead-active" markerWidth="4" markerHeight="4" refX="3" refY="2" orient="auto">
          <path d="M0,0 L0,4 L4,2 Z" fill={ACCENT_CLASSES[LOOP_NODES[activeIdx].accent].dot} />
        </marker>
      </defs>

      {/* 화살표 연결선 */}
      {arrowPaths.map((d, i) => {
        const isActive = i === activeIdx;
        return (
          <path
            key={i}
            d={d}
            fill="none"
            stroke={isActive ? ACCENT_CLASSES[LOOP_NODES[activeIdx].accent].dot : 'var(--cds-border-strong-01)'}
            strokeWidth={isActive ? '0.9' : '0.6'}
            strokeDasharray={isActive ? 'none' : '2 1.5'}
            markerEnd={isActive ? 'url(#arrowhead-active)' : 'url(#arrowhead)'}
            opacity={isActive ? 1 : 0.4}
            style={{ transition: 'stroke 0.4s, stroke-width 0.4s, opacity 0.4s' }}
          />
        );
      })}

      {/* 노드 */}
      {positions.map((pos, i) => {
        const node = LOOP_NODES[i];
        const ac = ACCENT_CLASSES[node.accent];
        const isActive = i === activeIdx;
        return (
          <g key={node.id}>
            <circle
              cx={pos.x}
              cy={pos.y}
              r={isActive ? 7.5 : 6}
              fill={isActive ? ac.dot : 'var(--cds-layer-02)'}
              stroke={isActive ? ac.dot : 'var(--cds-border-strong-01)'}
              strokeWidth={isActive ? '0' : '0.5'}
              opacity={isActive ? 1 : 0.6}
              style={{ transition: 'r 0.4s, fill 0.4s, opacity 0.4s' }}
            />
            {/* 라벨 */}
            <text
              x={pos.x}
              y={pos.y + (i === 0 ? -10 : i === 2 ? 13 : 0)}
              dx={i === 1 ? 11 : i === 3 ? -11 : 0}
              textAnchor={i === 1 ? 'start' : i === 3 ? 'end' : 'middle'}
              dominantBaseline={i === 0 ? 'auto' : i === 2 ? 'auto' : 'middle'}
              fontSize="4.5"
              fontWeight={isActive ? '700' : '500'}
              fill={isActive ? ac.dot : 'var(--cds-text-helper)'}
              style={{ transition: 'fill 0.4s, font-weight 0.3s', fontFamily: 'var(--font-sans)' }}
            >
              {node.label}
            </text>
          </g>
        );
      })}

      {/* 중심 로고 */}
      <circle cx={CX} cy={CY} r={7} fill="var(--cds-interactive)" />
      <text x={CX} y={CY} textAnchor="middle" dominantBaseline="middle" fontSize="4" fontWeight="700" fill="#fff" fontFamily="var(--font-sans)">
        RICE
      </text>
    </svg>
  );
};

/** 노드 레이블 + 설명을 교체 표시하는 패널 */
const LoopDescription: React.FC<{ node: LoopNode; idx: number }> = ({ node, idx }) => {
  const ac = ACCENT_CLASSES[node.accent];
  return (
    <div
      key={idx}
      className="flex flex-col gap-2"
      style={{ animation: 'onbStep 280ms ease', animationFillMode: 'both' }}
    >
      <div className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-full w-fit ${ac.bg}`}>
        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0`} style={{ backgroundColor: ac.dot }} />
        <span className={`text-caption-01 font-semibold ${ac.text}`}>{node.label}</span>
        <span className="text-caption-01 text-text-helper">— {node.sublabel}</span>
      </div>
      <p className="text-body-short-01 text-text-secondary leading-relaxed">{node.description}</p>
    </div>
  );
};

/** conceptStep에 들어가는 전체 다이어그램 섹션 */
const LearningLoopDiagram: React.FC = () => {
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    const id = setInterval(
      () => setActiveIdx((i) => (i + 1) % LOOP_NODES.length),
      2400,
    );
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex flex-col gap-5 w-full">
      <div>
        <h2 className="text-heading-03 font-semibold text-text-primary">쓸수록 똑똑해지는 이유</h2>
        <p className="text-body-short-01 text-text-secondary mt-1.5">
          RICE의 자기 개선 루프가 반복될수록 작업이 빨라지고 정확해져요.
        </p>
      </div>

      <div className="flex gap-6 items-center">
        {/* 다이어그램 — 크게 */}
        <div className="w-[240px] h-[240px] flex-shrink-0">
          <LoopDiagram activeIdx={activeIdx} />
        </div>

        {/* 설명 패널 */}
        <div className="flex-1 min-w-0">
          <LoopDescription node={LOOP_NODES[activeIdx]} idx={activeIdx} />

          {/* 단계 인디케이터 (클릭 가능) */}
          <div className="flex items-center gap-2 mt-5">
            {LOOP_NODES.map((node, i) => {
              const ac = ACCENT_CLASSES[node.accent];
              return (
                <button
                  key={node.id}
                  onClick={() => setActiveIdx(i)}
                  title={node.label}
                  className={`h-1.5 rounded-full transition-all duration-300 ${
                    i === activeIdx ? `w-6 ${ac.bg.replace('/15', '/80').replace('/10', '/60')}` : 'w-1.5 bg-border-strong-01 hover:bg-border-strong-02'
                  }`}
                />
              );
            })}
          </div>
        </div>
      </div>

      {/* 4개 노드 요약 칩 */}
      <div className="grid grid-cols-2 gap-2">
        {LOOP_NODES.map((node, i) => {
          const ac = ACCENT_CLASSES[node.accent];
          const isActive = i === activeIdx;
          return (
            <button
              key={node.id}
              onClick={() => setActiveIdx(i)}
              className={`flex items-center gap-2.5 px-3 py-2.5 rounded-xl border text-left transition-all duration-200 ${
                isActive
                  ? `border-transparent ring-1 ${ac.ring} ${ac.bg}`
                  : 'border-border-subtle-01 bg-layer-02 hover:border-border-strong-01'
              }`}
            >
              <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: ac.dot, opacity: isActive ? 1 : 0.5 }} />
              <div className="min-w-0">
                <div className={`text-body-short-01 font-semibold ${isActive ? ac.text : 'text-text-secondary'}`}>
                  {node.label}
                </div>
                <div className="text-caption-01 text-text-helper leading-tight mt-0.5">{node.sublabel}</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
};

// ── 스텝 정의 ─────────────────────────────────────────────────────────────

/* ── 1. 환영 ── */
const welcomeStep: OnboardingStep = {
  id: 'welcome',
  navLabel: '환영',
  navDescription: 'RICE 소개',
  render: () => (
    <div className="flex flex-col w-full">
      <h1 className="text-heading-04 font-semibold text-text-primary leading-tight">
        나와 함께 똑똑해지는
        <br />
        AI 워크스페이스, RICE
      </h1>
      <p className="text-body-long-01 text-text-secondary mt-3">
        RICE는 단순한 챗봇이 아니에요. 대화하며 일할수록{' '}
        <span className="text-text-primary font-medium">내 작업 방식을 학습</span>하고, 반복되는 일은{' '}
        <span className="text-text-primary font-medium">스킬로 자동화</span>해 주는 자기개선형 에이전트입니다.
      </p>
      <p className="text-caption-01 text-text-helper mt-2">
        모든 데이터와 설정은 이 기기에만 저장돼요. 1분이면 설정이 끝납니다.
      </p>

      {/* 채팅 미리보기 */}
      <WelcomeChatPreview />
    </div>
  ),
};

/* ── 2. 동작 원리 ── */
const conceptStep: OnboardingStep = {
  id: 'concept',
  navLabel: '동작 원리',
  navDescription: '자기 개선 루프',
  render: () => (
    <div className="w-full">
      <LearningLoopDiagram />
    </div>
  ),
};

/* ── 3. 생태계 소개 (Skill / Plugin / Connector) ── */

/** 생태계 카드 한 장.
 *  desc 영역을 flex-1로 늘려서 카드 높이가 같을 때 items가 항상 같은 수직 위치에 오도록 한다. */
const EcoCard: React.FC<{
  accent: 'blue' | 'teal' | 'purple';
  icon: React.ReactNode;
  badge: string;
  title: string;
  desc: string;
  items: string[];
}> = ({ accent, icon, badge, title, desc, items }) => {
  const accentMap = {
    blue:   { bg: 'bg-blue-60/12',   text: 'text-blue-50',   border: 'border-blue-60/20',   dot: '#4589ff' },
    teal:   { bg: 'bg-teal-60/12',   text: 'text-teal-60',   border: 'border-teal-60/20',   dot: '#009d9a' },
    purple: { bg: 'bg-purple-60/12', text: 'text-purple-60', border: 'border-purple-60/20', dot: '#8a3ffc' },
  } as const;
  const ac = accentMap[accent];
  return (
    <div className={`flex-1 min-w-0 rounded-xl border ${ac.border} bg-layer-02 p-4 flex flex-col`}>
      {/* 아이콘 + 뱃지 */}
      <div className="flex items-center gap-2.5 mb-3">
        <span className={`flex items-center justify-center w-9 h-9 rounded-lg flex-shrink-0 ${ac.bg}`}>
          <span className={ac.text}>{icon}</span>
        </span>
        <div>
          <div className={`text-[10px] font-bold uppercase tracking-widest ${ac.text}`}>{badge}</div>
          <div className="text-body-short-01 font-semibold text-text-primary leading-tight">{title}</div>
        </div>
      </div>
      {/* 설명 — flex-1로 늘려 items를 항상 하단 같은 위치에 고정 */}
      <p className="text-caption-01 text-text-secondary leading-relaxed flex-1">{desc}</p>
      {/* 구분선 */}
      <div className="border-t border-border-subtle-01 my-3" />
      {/* 예시 항목 */}
      <ul className="flex flex-col gap-1">
        {items.map((item) => (
          <li key={item} className="flex items-center gap-1.5 text-caption-01 text-text-helper">
            <span className="w-1 h-1 rounded-full flex-shrink-0" style={{ backgroundColor: accentMap[accent].dot }} />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
};

const ecosystemStep: OnboardingStep = {
  id: 'ecosystem',
  navLabel: '생태계',
  navDescription: 'Skill · Plugin · Connector',
  render: () => (
    <div className="flex flex-col w-full gap-5">
      <div>
        <h2 className="text-heading-03 font-semibold text-text-primary">RICE 생태계 3가지</h2>
        <p className="text-body-short-01 text-text-secondary mt-1.5">
          대화 너머의 기능들 — 설치하고 연결할수록 에이전트가 강해져요.
        </p>
      </div>

      <div className="flex gap-3">
        <EcoCard
          accent="blue"
          badge="Skill"
          icon={
            <LineIcon size={18}>
              <path d="M12 3l8 3v6c0 5-3.4 7.7-8 9-4.6-1.3-8-4-8-9V6l8-3z"/>
              <path d="M9 12l2 2 4-4"/>
            </LineIcon>
          }
          title="스킬"
          desc="개인이 만든 워크플로우예요. 반복 작업을 절차로 저장해 재사용하며, 내 대화에서 자동 생성되거나 Skill Market에서 설치할 수 있어요."
          items={['/ 명령으로 즉시 실행', '학습으로 자동 개선', 'Skill Market에서 공유']}
        />
        <EcoCard
          accent="purple"
          badge="Plugin"
          icon={
            <LineIcon size={18}>
              <rect x="3" y="3" width="7" height="7" rx="1"/>
              <rect x="14" y="3" width="7" height="7" rx="1"/>
              <rect x="3" y="14" width="7" height="7" rx="1"/>
              <path d="M14 17.5h7M17.5 14v7"/>
            </LineIcon>
          }
          title="플러그인"
          desc="솔루션팀·조직이 패키징한 도메인 전문성이에요. Skills + Connector를 묶어 배포하며, 설치하면 에이전트가 해당 분야 전문가가 돼요."
          items={['WXO · 법률 · 트러블슈팅', '도메인 컨텍스트 이해', 'Plugin Market에서 배포']}
        />
        <EcoCard
          accent="teal"
          badge="Connector"
          icon={
            <LineIcon size={18}>
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
            </LineIcon>
          }
          title="커넥터"
          desc="에이전트가 외부 서비스에 접근하는 통로예요. Plugin manifest의 requires.connectors 항목을 OAuth로 인증하면 Plugin이 자동으로 활용해요."
          items={['Google Drive · Notion 등', 'OAuth 2.0 인증', 'Plugin이 자동 요청']}
        />
      </div>
    </div>
  ),
};

/* ── 4. 모델 연결 (필수) ── */
const modelStep: OnboardingStep = {
  id: 'model',
  navLabel: '모델 연결',
  navDescription: '필수 단계',
  primaryLabel: '연결 완료',
  canAdvance: (ctx) => ctx.draft.canSave,
  blockedHint: (ctx) =>
    ctx.draft.mode === 'local'
      ? '로컬 모델을 확인하고 선택하면 계속할 수 있어요'
      : 'API 모델을 추가하면 계속할 수 있어요',
  render: (ctx) => (
    <div className="flex flex-col max-w-[480px]">
      <h2 className="text-heading-03 font-semibold text-text-primary">AI 모델 연결</h2>
      <p className="text-body-short-01 text-text-secondary mt-2 mb-5">
        클라우드 API(SaaS) 또는 내 기기의 로컬 모델(Ollama) 중 하나를 선택하세요. 나중에 설정에서 언제든 바꿀 수 있어요.
      </p>
      <div className="bg-layer-01 border border-border-subtle-01 rounded-xl p-5 shadow-elevation-1">
        <ModelPicker
          currentModel={ctx.draft.modelId || ctx.initial.currentModel}
          modelMode={ctx.draft.mode}
          settings={ctx.draft.settings}
          onboarding
          onChange={ctx.setDraft}
        />
      </div>
    </div>
  ),
};

/* ── 5. 준비 완료 ── */
const readyStep: OnboardingStep = {
  id: 'ready',
  navLabel: '준비 완료',
  navDescription: '시작하기',
  primaryLabel: '시작하기',
  render: (ctx) => (
    <div className="flex flex-col w-full gap-6">
      {/* 헤더: 체크 아이콘을 제목 오른쪽에 나란히 배치 */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h2 className="text-heading-03 font-semibold text-text-primary">모두 준비됐어요</h2>
          <p className="text-body-short-01 text-text-secondary">
            <span className="font-mono text-text-primary">{ctx.draft.modelId || ctx.initial.currentModel}</span>
            {' '}모델이 연결됐어요. 바로 대화를 시작할 수 있어요.
          </p>
        </div>
        <span className="flex items-center justify-center w-12 h-12 rounded-2xl bg-support-success/15 text-support-success flex-shrink-0">
          <LineIcon size={26}>{icons.check}</LineIcon>
        </span>
      </div>

      {/* 팁 목록 */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-layer-02 text-body-short-01 text-text-secondary">
          <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-layer-03 text-blue-50 flex-shrink-0">
            <LineIcon size={15}>{icons.slash}</LineIcon>
          </span>
          <span>
            <span className="text-text-primary font-medium">슬래시(/) 명령</span>으로 저장된 스킬을 즉시 실행해요
          </span>
        </div>
        <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-layer-02 text-body-short-01 text-text-secondary">
          <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-layer-03 text-teal-60 flex-shrink-0">
            <LineIcon size={15}>{icons.brain}</LineIcon>
          </span>
          <span>
            대화를 이어갈수록 <span className="text-text-primary font-medium">학습 패널</span>에 내 작업 패턴이 쌓여요
          </span>
        </div>
        <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-layer-02 text-body-short-01 text-text-secondary">
          <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-layer-03 text-purple-60 flex-shrink-0">
            <LineIcon size={15}>{icons.shield}</LineIcon>
          </span>
          <span>
            모든 데이터는 <span className="text-text-primary font-medium">이 기기에만</span> 저장돼요
          </span>
        </div>
      </div>
    </div>
  ),
};

/** 기본 온보딩 스텝 순서 */
export const buildSteps = (): OnboardingStep[] => [welcomeStep, conceptStep, ecosystemStep, modelStep, readyStep];

// Made with Bob
