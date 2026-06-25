import React, { useEffect, useMemo } from 'react';
import { Button, Icon } from '../ui';
import { dialogBackdropCls } from '../ui/surfaces';
import type { Skill, SkillDetail } from '../../lib/learningData';
import { ownedSkillFiles } from '../../lib/skillBridge';

/**
 * SkillPublishDialog — Skill Market 공개 전용 흐름.
 * 공유는 외부로 나가는 비가역적 행위라, 일반 ConfirmDialog 대신 "무엇이 공개되는지"를
 * 마켓 카드 미리보기로 보여주고 한 번 더 묻는다. (표시·확인만 담당 — 실제 발행 부수효과는 호출부 소유)
 */
export interface SkillPublishDialogProps {
  open: boolean;
  skill: Skill;
  detail: SkillDetail;
  onConfirm: () => void;
  onClose: () => void;
}

export const SkillPublishDialog: React.FC<SkillPublishDialogProps> = ({ open, skill, detail, onConfirm, onClose }) => {
  // Esc 로 닫기
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [open, onClose]);

  // 학습형(synthesized) 스킬은 파일이 자동 합성된 SKILL.md 한 장뿐이라 "파일 1"은 빈약해 보인다.
  // 이 경우 파일 수를 숨기고, 실제 번들 파일을 가진 스킬에서만 노출한다.
  const { fileCount, synthesized } = useMemo(() => {
    if (!open) return { fileCount: 0, synthesized: true };
    const f = ownedSkillFiles(skill, detail);
    return { fileCount: f.files.length, synthesized: f.synthesized };
  }, [open, skill, detail]);
  const ruleCount = detail.rules.length;
  // 설치형 재공유면 원작자 attribution을 명시 (의존성 없이 내부 계산)
  const origin = skill.origin ?? 'learned';
  const reshareOf = origin === 'installed' ? skill.upstream : undefined;

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      {/* backdrop */}
      <div className={dialogBackdropCls} onClick={onClose} aria-hidden="true" />

      {/* dialog */}
      <div
        role="dialog"
        aria-modal="true"
        className="relative w-full max-w-md mx-4 bg-layer-01 border border-border-subtle-01 rounded-2xl shadow-overlay animate-scale-in overflow-hidden"
      >
        {/* header */}
        <div className="flex items-center gap-2.5 px-5 pt-5 pb-3">
          <span className="flex-shrink-0 w-8 h-8 rounded-full bg-purple-60/15 flex items-center justify-center">
            <Icon name="share" size={16} className="text-purple-60" />
          </span>
          <div className="min-w-0">
            <h2 className="text-body-short-02 font-semibold text-text-primary">Skill Market에 공개</h2>
            <p className="text-caption-01 text-text-helper">아래 내용이 마켓에 이렇게 보여요</p>
          </div>
        </div>

        {/* market card preview — 발행될 모습 */}
        <div className="px-5">
          <div className="rounded-xl border border-border-subtle-01 bg-background p-3.5">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-lg bg-blue-60">
                <Icon name="skill" size={16} className="text-white" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-body-short-01 font-semibold text-text-primary truncate">{skill.name}</span>
                  <span className="text-caption-01 font-mono text-text-secondary bg-layer-02 border border-border-subtle-01 rounded px-1.5 py-px flex-shrink-0">
                    {skill.ver}
                  </span>
                </div>
                <div className="text-caption-01 text-text-helper">작성자 · 나</div>
              </div>
            </div>
            <p className="mt-2.5 text-caption-01 text-text-secondary leading-4 line-clamp-2">{detail.summary}</p>
            <div className="mt-2.5 pt-2.5 border-t border-border-subtle-01 flex items-center gap-3 text-caption-01 text-text-helper">
              {!synthesized && (
                <span className="inline-flex items-center gap-1">
                  <Icon name="document" size={12} /> 파일 {fileCount}
                </span>
              )}
              <span className="inline-flex items-center gap-1">
                <Icon name="rule" size={12} /> 규칙 {ruleCount}
              </span>
            </div>
            {reshareOf && (
              <div className="mt-2.5 pt-2.5 border-t border-border-subtle-01 flex items-center gap-1.5 text-caption-01">
                <Icon name="download" size={12} className="text-link-primary flex-shrink-0" />
                <span className="text-text-helper">
                  <span className="text-text-secondary font-medium">{reshareOf.author}</span>
                  {reshareOf.team ? ` · ${reshareOf.team}` : ''} 스킬 기준 재공유 — 원작 표기가 유지돼요
                </span>
              </div>
            )}
          </div>
        </div>

        {/* notice */}
        <div className="px-5 py-3">
          <div className="flex items-start gap-2 text-caption-01 text-text-secondary leading-4">
            <Icon name="idea" size={14} className="text-icon-primary flex-shrink-0 mt-px" />
            <span>공개하면 다른 사용자가 설치·사용할 수 있어요. 언제든 마켓에서 다시 내릴 수 있어요.</span>
          </div>
        </div>

        {/* footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-2.5 border-t border-border-subtle-01 bg-background">
          <button
            onClick={onClose}
            className="h-7 px-3 rounded-lg text-body-short-01 font-semibold text-text-primary bg-layer-02 border border-border-strong-01 hover:bg-layer-hover-01 transition-colors"
          >
            취소
          </button>
          <Button kind="primary" size="sm" className="!h-7" icon={<Icon name="share" size={14} />} onClick={onConfirm}>
            Market에 공개
          </Button>
        </div>
      </div>
    </div>
  );
};

// Made with Bob
