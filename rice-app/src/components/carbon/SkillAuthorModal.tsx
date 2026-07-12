import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Button, Icon, Modal } from '../ui';
import { SkillCard } from './SkillCard';
import { skillCategoryCodes, type SkillCategoryCode } from '../../lib/mockData';
import type { SkillDraft } from '../../hooks/useMarketplace';
import type { Skill } from '../../lib/types';

/**
 * SkillAuthorModal — "나의 스킬 업로드" 게시 마법사.
 *
 * 스킬 마켓 좌측 레일의 업로드 버튼에서 진입한다. 세 단계로 나뉜 위저드:
 *   1. 기본 정보 — 이름 · 설명 · 카테고리 · 태그 (SKILL.md 파일로 시작도 가능)
 *   2. 스킬 정의 — SKILL.md 본문 편집(기본 정보로 자동 생성) · 버전 · 파일 불러오기
 *   3. 검토 · 게시 — 마켓 카드 실시간 미리보기 + 공개 범위 확인 후 게시
 *
 * 단계 게이트:
 *   - 1→2 : 이름·설명 입력 + 카테고리 선택
 *   - 2→3 : SKILL.md 본문이 비어있지 않음
 *   - 게시 : 위 두 게이트 모두 통과
 * 게시 후에는 동일 모달 안에서 완료 화면(생성된 카드 + "마켓에서 보기")을 보여준다.
 */

const CATEGORY_CODES = skillCategoryCodes.filter((c) => c !== 'all') as SkillCategoryCode[];
const MAX_TAGS = 6;

/**
 * 카테고리 code → 번역 함수.
 * t()의 키 타입이 정적이라 템플릿 리터럴로 동적 조회 불가 → 명시적 맵으로 처리.
 */
function getCategoryLabel(code: SkillCategoryCode, t: TFunction<'skills'>): string {
  const map: Record<SkillCategoryCode, () => string> = {
    all: () => t('market.categoryAll'),
    dataAnalysis: () => t('market.categoryDataAnalysis'),
    automation: () => t('market.categoryAutomation'),
    integration: () => t('market.categoryIntegration'),
    dev: () => t('market.categoryDev'),
    communication: () => t('market.categoryCommunication'),
    productivity: () => t('market.categoryProductivity'),
  };
  return map[code]?.() ?? code;
}

/**
 * 추천 태그: 카탈로그의 쉼표 구분 문자열을 배열로 변환.
 * 동적 키 조회 불가 → 명시적 맵으로 처리.
 */
function getTagSuggestions(code: SkillCategoryCode | '', t: TFunction<'skills'>): string[] {
  if (!code || code === 'all') return [];
  const map: Record<SkillCategoryCode, () => string> = {
    all: () => '',
    dataAnalysis: () => t('author.tagSuggestionDataAnalysis'),
    automation: () => t('author.tagSuggestionAutomation'),
    integration: () => t('author.tagSuggestionIntegration'),
    dev: () => t('author.tagSuggestionDev'),
    communication: () => t('author.tagSuggestionCommunication'),
    productivity: () => t('author.tagSuggestionProductivity'),
  };
  const raw = map[code]?.() ?? '';
  return raw.split(',').map((s) => s.trim()).filter(Boolean);
}

const inputCls =
  'w-full h-9 px-3 bg-field-01 text-text-primary text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder';

// 한글 이름이 많은 제품이라 Hangul을 보존한다(예: "회의록 자동 요약" → "회의록-자동-요약").
const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9가-힣]+/g, '-')
    .replace(/(^-|-$)/g, '');

/** SKILL.md 프론트매터에서 name/description/version을 추출 */
function readFrontmatter(text: string) {
  const fm = text.match(/^---\s*\n([\s\S]*?)\n---/);
  if (!fm) return {};
  const get = (k: string) => {
    const m = fm[1].match(new RegExp(`^${k}\\s*:\\s*(.+)$`, 'm'));
    return m ? m[1].trim().replace(/^["']|["']$/g, '') : '';
  };
  return { name: get('name'), description: get('description'), version: get('version') };
}

export interface SkillAuthorModalProps {
  open: boolean;
  onClose: () => void;
  /** 게시 확정 시 호출 — 생성된 스킬을 반환받아 미리보기에 사용 */
  onPublish: (draft: SkillDraft) => Skill;
  /** 완료 화면에서 "마켓에서 보기"를 눌렀을 때 */
  onViewInMarket?: (skill: Skill) => void;
}

export const SkillAuthorModal: React.FC<SkillAuthorModalProps> = ({
  open,
  onClose,
  onPublish,
  onViewInMarket,
}) => {
  const { t } = useTranslation('skills');

  const STEPS = [
    t('author.step0Label'),
    t('author.step1Label'),
    t('author.step2Label'),
  ] as const;

  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  // category 상태는 stable code ('dataAnalysis' 등) 저장 — 렌더 시 t()로 표시
  const [category, setCategory] = useState<SkillCategoryCode | ''>('');
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  const [version, setVersion] = useState('1.0.0');
  const [team, setTeam] = useState('');
  const [definition, setDefinition] = useState('');
  // 본문을 사용자가 직접 손대기(또는 파일 로드) 전까지는 기본 정보로 자동 동기화
  const [defTouched, setDefTouched] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [published, setPublished] = useState<Skill | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);

  // 모달이 열릴 때마다 폼 초기화
  useEffect(() => {
    if (open) {
      setStep(0);
      setName('');
      setDescription('');
      setCategory('');
      setTags([]);
      setTagInput('');
      setVersion('1.0.0');
      setTeam('');
      setDefinition('');
      setDefTouched(false);
      setFileName(null);
      setDragOver(false);
      setPublished(null);
    }
  }, [open]);

  // 기본 정보로부터 자동 생성되는 SKILL.md 템플릿 (모델용 프롬프트 — 번역 대상 아님)
  const generatedDef = useMemo(() => {
    const slug = slugify(name) || 'my-skill';
    const tagBlock = tags.length
      ? tags.map((tag) => `- ${tag}`).join('\n')
      : '- 어떤 상황에서 이 스킬을 쓰면 좋은지 적어주세요.';
    return `---
name: ${slug}
description: ${description.trim() || '한 줄 설명을 적어주세요.'}
version: ${version.trim() || '1.0.0'}
---

# ${name.trim() || '스킬 이름'}

${description.trim() || '이 스킬이 무엇을 하는지 설명하세요.'}

## 사용 시점

${tagBlock}

## 동작 방식

1. 필요한 입력을 모읍니다.
2. 작업을 단계별로 수행합니다.
3. 결과를 검토하고 다듬습니다.
`;
  }, [name, description, version, tags]);

  // 실제로 게시될 본문
  const effectiveDef = defTouched ? definition : generatedDef;

  const trimmedName = name.trim();
  const step0Valid = trimmedName.length > 0 && description.trim().length > 0 && category.length > 0;
  const step1Valid = effectiveDef.trim().length > 0;
  const canPublish = step0Valid && step1Valid;

  // i단계로 이동 가능한지(이전 게이트가 모두 통과해야 앞으로 이동 가능)
  const canReach = (i: number) => (i <= 0 ? true : i === 1 ? step0Valid : step0Valid && step1Valid);
  const goStep = (i: number) => {
    if (canReach(i)) setStep(i);
  };

  // ── 태그 칩 입력 ──
  const addTag = (raw: string) => {
    const trimTag = raw.trim().replace(/^#/, '');
    if (!trimTag || tags.includes(trimTag) || tags.length >= MAX_TAGS) {
      setTagInput('');
      return;
    }
    setTags((prev) => [...prev, trimTag]);
    setTagInput('');
  };
  const onTagKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTag(tagInput);
    } else if (e.key === 'Backspace' && !tagInput && tags.length) {
      setTags((prev) => prev.slice(0, -1));
    }
  };

  // 추천 태그 — 함수 분리로 타입 안전 동적 조회
  const suggestions = useMemo(
    () => getTagSuggestions(category, t).filter((s) => !tags.includes(s)),
    [category, tags, t],
  );

  // ── 파일 불러오기 (SKILL.md) ──
  const applyFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? '');
      setDefinition(text);
      setDefTouched(true);
      setFileName(file.name);
      const fm = readFrontmatter(text);
      if (fm.name && !name.trim()) setName(fm.name);
      if (fm.description && !description.trim()) setDescription(fm.description);
      if (fm.version) setVersion(fm.version);
    };
    reader.readAsText(file);
  };
  const onPickFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) applyFile(f);
    e.target.value = ''; // 같은 파일 재선택 허용
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) applyFile(f);
  };

  const handlePublish = () => {
    if (!canPublish || !category) return;
    const skill = onPublish({
      name: trimmedName,
      description: description.trim(),
      category, // stable code 저장
      tags,
      version: version.trim() || '1.0.0',
      team: team.trim() || undefined,
      definition: effectiveDef,
    });
    setPublished(skill);
  };

  // 비활성 "다음/게시" 위에 hover 시 안내 문구
  const blockedReason =
    step === 0 ? t('author.blocked0') : step === 1 ? t('author.blocked1') : '';

  // ───────────────────────── 완료 화면 ─────────────────────────
  if (published) {
    const successFooter = (
      <div className="flex items-center justify-between w-full">
        <Button kind="ghost" size="sm" onClick={onClose} className="!h-8">
          {t('author.doneCloseBtn')}
        </Button>
        <Button
          kind="primary"
          size="sm"
          icon={<Icon name="arrow-right" size={15} />}
          onClick={() => onViewInMarket?.(published)}
          className="!h-8"
        >
          {t('author.doneViewBtn')}
        </Button>
      </div>
    );
    return (
      <Modal open={open} onClose={onClose} title={t('author.doneTitle')} size="md" footer={successFooter}>
        <div className="flex flex-col items-center text-center px-2">
          {/* 체크 배지 */}
          <div className="relative mb-4">
            <div className="absolute inset-0 rounded-full bg-support-success/20 blur-xl" aria-hidden="true" />
            <div className="relative w-16 h-16 flex items-center justify-center rounded-full bg-support-success/15 ring-1 ring-support-success/40 animate-scale-in">
              <Icon name="check" size={32} className="text-support-success" />
            </div>
          </div>
          <h3 className="text-heading-03 font-semibold text-text-primary">{t('author.doneHeading')}</h3>
          <p className="mt-1.5 text-body-short-01 text-text-secondary max-w-sm">
            {t('author.doneBody', { name: published.name })}
          </p>

          {/* 게시된 카드 미리보기 */}
          <div className="mt-6 w-full max-w-[360px] pointer-events-none select-none">
            <SkillCard {...published} onInstall={() => {}} onSponsor={() => {}} />
          </div>
        </div>
      </Modal>
    );
  }

  // ───────────────────────── 위저드 푸터 ─────────────────────────
  const footer = (
    <div className="flex items-center justify-between w-full">
      {step === 0 ? (
        <Button kind="ghost" size="sm" onClick={onClose} className="!h-8">
          {t('author.cancelBtn')}
        </Button>
      ) : (
        <Button
          kind="ghost"
          size="sm"
          icon={<Icon name="arrow-right" size={15} className="rotate-180" />}
          onClick={() => goStep(step - 1)}
          className="!h-8"
        >
          {t('author.backBtn')}
        </Button>
      )}

      <div className="relative group">
        {step < 2 ? (
          <Button
            kind="primary"
            size="sm"
            onClick={() => goStep(step + 1)}
            disabled={step === 0 ? !step0Valid : !step1Valid}
            className="!h-8"
          >
            {t('author.nextBtn')}
            <Icon name="arrow-right" size={15} className="ml-1.5" />
          </Button>
        ) : (
          <Button
            kind="primary"
            size="sm"
            onClick={handlePublish}
            disabled={!canPublish}
            icon={<Icon name="share" size={15} />}
            className="!h-8"
          >
            {t('author.publishBtn')}
          </Button>
        )}
        {blockedReason && (step === 0 ? !step0Valid : step === 1 ? !step1Valid : false) && (
          <div className="pointer-events-none absolute bottom-full right-0 mb-2 w-max max-w-[240px] px-2.5 py-1.5 rounded-md bg-text-primary text-text-inverse text-caption-01 shadow-elevation-2 opacity-0 translate-y-1 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-150">
            {blockedReason}
            <span className="absolute top-full right-4 -mt-px border-4 border-transparent border-t-text-primary" />
          </div>
        )}
      </div>
    </div>
  );

  return (
    <Modal open={open} onClose={onClose} title={t('author.modalTitle')} size="md" footer={footer}>
      {/* 숨겨진 파일 입력 — 단계 1·2 양쪽에서 트리거 */}
      <input
        ref={fileRef}
        type="file"
        accept=".md,.markdown,.txt"
        onChange={onPickFile}
        className="hidden"
      />

      {/* ── 스텝 인디케이터 — compact, ring 없이 ── */}
      <div className="flex items-center mb-5">
        {STEPS.map((label, i) => {
          const state = i < step ? 'done' : i === step ? 'current' : 'todo';
          const reachable = canReach(i);
          return (
            <React.Fragment key={label}>
              <button
                type="button"
                onClick={() => goStep(i)}
                disabled={!reachable}
                className={`flex items-center gap-2 transition-opacity ${
                  reachable ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'
                }`}
              >
                <span
                  className={`w-6 h-6 flex items-center justify-center rounded-full text-caption-01 font-semibold transition-colors ${
                    state === 'current'
                      ? 'bg-interactive text-white'
                      : state === 'done'
                      ? 'bg-interactive text-white'
                      : 'bg-layer-02 text-text-helper border border-border-subtle-01'
                  }`}
                >
                  {state === 'done' ? <Icon name="check" size={13} /> : i + 1}
                </span>
                <span
                  className={`text-body-short-01 hidden sm:inline ${
                    state === 'current'
                      ? 'text-text-primary font-semibold'
                      : state === 'done'
                      ? 'text-text-secondary'
                      : 'text-text-helper'
                  }`}
                >
                  {label}
                </span>
              </button>
              {i < STEPS.length - 1 && (
                <span className={`flex-1 h-px mx-3 ${i < step ? 'bg-interactive' : 'bg-border-subtle-01'}`} />
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* ── 단계 1: 기본 정보 ── */}
      {step === 0 && (
        <div className="space-y-4 animate-fade-in">
          {/* SKILL.md로 시작 — 드롭/선택 */}
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg border border-dashed text-left transition-colors ${
              dragOver
                ? 'border-interactive bg-layer-selected-01'
                : 'border-border-strong-01 hover:border-border-strong-02 hover:bg-layer-hover-01'
            }`}
          >
            <span className="w-9 h-9 flex-shrink-0 flex items-center justify-center rounded-lg bg-layer-02 text-icon-secondary">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <path d="M17 8l-5-5-5 5M12 3v12" />
              </svg>
            </span>
            <div className="min-w-0">
              <div className="text-body-short-01 font-semibold text-text-primary">
                {fileName ? t('author.fileLoadedTitle', { fileName }) : t('author.filePickTitle')}
              </div>
              <div className="text-caption-01 text-text-helper truncate">
                {fileName ? t('author.fileLoadedHint') : t('author.filePickHint')}
              </div>
            </div>
            {fileName && <Icon name="check-circle" size={18} className="ml-auto text-support-success flex-shrink-0" />}
          </button>

          <div className="flex items-center gap-3">
            <span className="flex-1 h-px bg-border-subtle-01" />
            <span className="text-caption-01 text-text-helper">{t('author.dividerOr')}</span>
            <span className="flex-1 h-px bg-border-subtle-01" />
          </div>

          {/* 이름 */}
          <div>
            <label className="text-label-01 text-text-secondary mb-1.5 block">
              {t('author.fieldNameLabel')} <span className="text-support-error">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('author.fieldNamePlaceholder')}
              maxLength={48}
              className={inputCls}
            />
          </div>

          {/* 설명 */}
          <div>
            <label className="text-label-01 text-text-secondary mb-1.5 block">
              {t('author.fieldDescLabel')} <span className="text-support-error">*</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('author.fieldDescPlaceholder')}
              rows={2}
              maxLength={140}
              className={`${inputCls} h-auto py-2.5 resize-none leading-relaxed`}
            />
            <div className="text-caption-01 text-text-helper text-right mt-1">
              {t('author.fieldDescCounter', { count: description.length })}
            </div>
          </div>

          {/* 카테고리 */}
          <div>
            <label className="text-label-01 text-text-secondary mb-2 block">
              {t('author.fieldCategoryLabel')} <span className="text-support-error">*</span>
            </label>
            <div className="flex flex-wrap gap-1.5">
              {CATEGORY_CODES.map((code) => {
                const active = code === category;
                return (
                  <button
                    key={code}
                    type="button"
                    onClick={() => setCategory(code)}
                    aria-pressed={active}
                    className={`px-2.5 h-7 rounded-lg border text-body-short-01 transition-colors duration-150 ${
                      active
                        ? 'border-interactive bg-layer-selected-01 text-text-primary font-semibold'
                        : 'border-border-subtle-01 bg-background text-text-secondary hover:bg-layer-hover-01 hover:border-border-strong-01'
                    }`}
                  >
                    {getCategoryLabel(code, t)}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 태그 */}
          <div>
            <label className="text-label-01 text-text-secondary mb-1.5 block">
              {t('author.fieldTagLabel')}{' '}
              <span className="text-text-placeholder font-normal">{t('author.fieldTagMax', { max: MAX_TAGS })}</span>
            </label>
            <div
              className="flex flex-wrap items-center gap-1.5 min-h-10 px-2 py-1.5 bg-field-01 border border-border-strong-01 rounded-lg focus-within:border-focus focus-within:ring-1 focus-within:ring-focus transition-colors"
              onClick={(e) => (e.currentTarget.querySelector('input') as HTMLInputElement | null)?.focus()}
            >
              {tags.map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center gap-1 h-6 pl-2 pr-1 rounded-full bg-layer-02 ring-1 ring-inset ring-border-subtle-02 text-text-secondary text-caption-01"
                >
                  {tag}
                  <button
                    type="button"
                    onClick={() => setTags((prev) => prev.filter((x) => x !== tag))}
                    className="w-4 h-4 flex items-center justify-center rounded-full hover:bg-layer-hover-02"
                    aria-label={t('author.fieldTagRemoveAriaLabel', { tag })}
                  >
                    <Icon name="close" size={11} />
                  </button>
                </span>
              ))}
              {tags.length < MAX_TAGS && (
                <input
                  type="text"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={onTagKeyDown}
                  onBlur={() => addTag(tagInput)}
                  placeholder={tags.length ? t('author.fieldTagInputMorePlaceholder') : t('author.fieldTagInputPlaceholder')}
                  className="flex-1 min-w-[100px] h-7 bg-transparent text-body-short-01 text-text-primary outline-none placeholder:text-text-placeholder"
                />
              )}
            </div>
            {suggestions.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => addTag(s)}
                    className="inline-flex items-center gap-1 px-2 h-6 rounded-full border border-border-subtle-01 bg-layer-02 text-caption-01 text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
                  >
                    <Icon name="add" size={11} />
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 팀 (선택) */}
          <div>
            <label className="text-label-01 text-text-secondary mb-1.5 block">
              {t('author.fieldTeamLabel')}{' '}
              <span className="text-text-placeholder font-normal">{t('author.fieldTeamOptional')}</span>
            </label>
            <input
              type="text"
              value={team}
              onChange={(e) => setTeam(e.target.value)}
              placeholder={t('author.fieldTeamPlaceholder')}
              maxLength={32}
              className={inputCls}
            />
          </div>
        </div>
      )}

      {/* ── 단계 2: 스킬 정의 ── */}
      {step === 1 && (
        <div className="space-y-4 animate-fade-in">
          <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-layer-02 border border-border-subtle-01">
            <Icon name="idea" size={15} className="text-icon-secondary flex-shrink-0 mt-0.5" />
            <p className="text-caption-01 text-text-helper leading-relaxed">
              {t('author.step1Hint')}
            </p>
          </div>

          {/* 본문 헤더 + 액션 */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-body-short-01 text-text-primary">
              <Icon name="document" size={16} className="text-icon-secondary" />
              <span className="font-mono">SKILL.md</span>
              {!defTouched && (
                <span className="text-caption-01 text-text-helper">{t('author.defAutoLabel')}</span>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              {defTouched && (
                <button
                  type="button"
                  onClick={() => {
                    setDefTouched(false);
                    setFileName(null);
                  }}
                  className="inline-flex items-center gap-1 text-caption-01 text-text-secondary hover:text-text-primary transition-colors"
                >
                  <Icon name="renew" size={13} />
                  {t('author.defRegenBtn')}
                </button>
              )}
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="inline-flex items-center gap-1 px-2 h-7 rounded-md border border-border-subtle-01 bg-layer-02 text-caption-01 text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
              >
                <Icon name="export" size={13} />
                {t('author.defLoadFileBtn')}
              </button>
            </div>
          </div>

          <textarea
            value={effectiveDef}
            onChange={(e) => {
              setDefinition(e.target.value);
              setDefTouched(true);
            }}
            spellCheck={false}
            rows={14}
            className="w-full px-3.5 py-3 bg-field-01 text-text-primary font-mono text-caption-01 leading-relaxed border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus resize-none"
          />

          {/* 버전 + 번들 파일 */}
          <div className="flex items-end gap-4">
            <div className="w-32">
              <label className="text-label-01 text-text-secondary mb-1.5 block">{t('author.fieldVersionLabel')}</label>
              <input
                type="text"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                placeholder="1.0.0"
                className={`${inputCls} font-mono`}
              />
            </div>
            <div className="flex-1 flex items-center gap-2 h-10 px-3 rounded-lg bg-layer-02 border border-border-subtle-01">
              <Icon name="document" size={14} className="text-icon-secondary flex-shrink-0" />
              <span className="text-caption-01 text-text-secondary font-mono">SKILL.md</span>
              <span className="text-caption-01 text-text-helper">{t('author.defCharCount', { count: effectiveDef.length.toLocaleString() })}</span>
              <span className="ml-auto text-caption-01 text-text-helper">{t('author.defBundleNote')}</span>
            </div>
          </div>
        </div>
      )}

      {/* ── 단계 3: 검토 · 게시 ── */}
      {step === 2 && (
        <div className="space-y-4 animate-fade-in">
          <p className="text-body-short-01 text-text-secondary">
            {t('author.step2Preview')}
          </p>

          {/* 실시간 카드 미리보기 */}
          <div className="rounded-xl bg-background border border-border-subtle-01 p-5 flex justify-center">
            <div className="w-full max-w-[360px] pointer-events-none select-none">
              <SkillCard
                id="preview"
                name={trimmedName}
                description={description.trim()}
                author={t('author.previewAuthorMe')}
                team={team.trim() || undefined}
                downloads={0}
                rating={0}
                points={0}
                tags={tags}
                installed={false}
                onInstall={() => {}}
                onSponsor={() => {}}
              />
            </div>
          </div>

          {/* 요약 메타 */}
          <div className="grid grid-cols-2 gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
            {([
              [t('author.metaCategory'), category ? getCategoryLabel(category, t) : ''],
              [t('author.metaVersion'), version.trim() || '1.0.0'],
              [t('author.metaTags'), tags.length ? tags.join(', ') : t('author.metaTagsNone')],
              [t('author.metaDef'), t('author.metaDefValue', { count: effectiveDef.length.toLocaleString() })],
            ] as [string, string][]).map(([k, v]) => (
              <div key={k} className="bg-layer-01 px-3.5 py-2.5">
                <div className="text-caption-01 text-text-helper">{k}</div>
                <div className="text-body-short-01 text-text-primary truncate">{v}</div>
              </div>
            ))}
          </div>

          {/* 공개 범위 안내 */}
          <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-layer-02 border border-border-subtle-01">
            <Icon name="share" size={15} className="text-icon-secondary flex-shrink-0 mt-0.5" />
            <p className="text-caption-01 text-text-helper leading-relaxed">
              {t('author.scopeHint')}
              <br />
              <span className="text-text-placeholder">{t('author.scopeUnpublishHint')}</span>
            </p>
          </div>
        </div>
      )}
    </Modal>
  );
};

// Made with Bob
