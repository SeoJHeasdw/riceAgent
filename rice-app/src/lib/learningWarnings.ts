import type { TFunction } from 'i18next';

export const EXTERNAL_CONTENT_REVIEW_WARNING = 'external_content_review';

// Parameterized warnings arrive as "code:value" (learning/proposals.py) — the
// runtime never synthesizes natural language; the locale sentence lives here.
const MASKED_PREFIX = 'masked:';
const RISK_FLAG_PREFIX = 'risk_flag:';
const DESCRIPTION_CHANGE_PREFIX = 'description_change:';
const TAGS_CHANGE_PREFIX = 'tags_change:';
// Verification-harness warnings arrive as "verify:<check>[:<param>]"
// (learning/skill/verifier.py warning_lines).
const VERIFY_PREFIX = 'verify:';

// Proposals already persisted before warning codes were introduced may still
// carry the original Korean sentences. Translate those forms during migration.
const LEGACY_EXTERNAL_CONTENT_REVIEW_WARNING =
  '외부 콘텐츠(도구 결과)가 포함된 대화에서 생성됨 — 본문을 특히 주의 깊게 확인하세요';
const LEGACY_MASKED_SUFFIX = ' 자동 마스킹됨';
const LEGACY_RISK_FLAG_PREFIX = '위험 플래그: ';
const LEGACY_DESCRIPTION_CHANGE_PREFIX = '설명 변경 포함: ';
const LEGACY_TAGS_CHANGE_PREFIX = '태그 변경 포함: ';
const LEGACY_VERIFY_PREFIX = '검증 경고: ';

const stripQuotes = (text: string): string => text.replace(/^[“"]/, '').replace(/[”"]$/, '');

// scan_risks (learning/skill/guard.py) label codes → locale keys. Unknown values
// (legacy Korean labels persisted before the codes) pass through unchanged.
const RISK_LABEL_KEYS: Record<string, string> = {
  prompt_injection: 'learning.riskPromptInjection',
  system_prompt_override: 'learning.riskSystemPromptOverride',
  hidden_instruction: 'learning.riskHiddenInstruction',
  credential_exfiltration: 'learning.riskCredentialExfiltration',
  destructive_shell_rm: 'learning.riskDestructiveShellRm',
  destructive_shell_mkfs: 'learning.riskDestructiveShellMkfs',
  destructive_shell_dd: 'learning.riskDestructiveShellDd',
  fork_bomb: 'learning.riskForkBomb',
  remote_script_execution: 'learning.riskRemoteScriptExecution',
  network_listener: 'learning.riskNetworkListener',
  reverse_shell: 'learning.riskReverseShell',
  ssh_key_persistence: 'learning.riskSshKeyPersistence',
  hidden_characters: 'learning.riskHiddenCharacters',
};

/** Risk label code → locale text. Also used by TransparencyPanel for the
 *  memory_blocked ledger events, which carry the same codes. */
export const localizeRiskLabel = (label: string, t: TFunction<'chat'>): string => {
  const key = RISK_LABEL_KEYS[label];
  if (!key) return label;
  // 동적 키라 타입드 리소스 유니온을 넓혀야 함 — TransparencyPanel의 TranslateWide와 동일 패턴
  return (t as unknown as (k: string) => string)(key);
};

// verify_candidate (learning/skill/verifier.py) check slugs → locale keys.
// Unknown checks pass through raw so a new backend check never renders blank.
const VERIFY_CHECK_KEYS: Record<string, string> = {
  'thin-body': 'learning.verifyThinBody',
  'no-steps': 'learning.verifyNoSteps',
  'dangling-ending': 'learning.verifyDanglingEnding',
  placeholder: 'learning.verifyPlaceholder',
  'unknown-tool': 'learning.verifyUnknownTool',
  'no-description': 'learning.verifyNoDescription',
  'near-duplicate': 'learning.verifyNearDuplicate',
  'patch-shrink': 'learning.verifyPatchShrink',
};

const localizeVerifyWarning = (rest: string, t: TFunction<'chat'>): string => {
  const sep = rest.indexOf(':');
  const check = sep === -1 ? rest : rest.slice(0, sep);
  const param = sep === -1 ? '' : rest.slice(sep + 1);
  const key = VERIFY_CHECK_KEYS[check];
  const tw = t as unknown as (k: string, o?: Record<string, unknown>) => string;
  if (!key) return `${VERIFY_PREFIX}${rest}`;
  return tw('learning.warningVerify', { text: tw(key, { param }) });
};

export const localizeLearningWarning = (warning: string, t: TFunction<'chat'>): string => {
  if (
    warning === EXTERNAL_CONTENT_REVIEW_WARNING ||
    warning === LEGACY_EXTERNAL_CONTENT_REVIEW_WARNING
  ) {
    return t('learning.warningExternalContent');
  }
  if (warning.startsWith(MASKED_PREFIX)) {
    return t('learning.warningMasked', { label: warning.slice(MASKED_PREFIX.length) });
  }
  if (warning.startsWith(RISK_FLAG_PREFIX)) {
    return t('learning.warningRiskFlag', {
      label: localizeRiskLabel(warning.slice(RISK_FLAG_PREFIX.length), t),
    });
  }
  if (warning.startsWith(DESCRIPTION_CHANGE_PREFIX)) {
    return t('learning.warningDescriptionChange', {
      text: warning.slice(DESCRIPTION_CHANGE_PREFIX.length),
    });
  }
  if (warning.startsWith(TAGS_CHANGE_PREFIX)) {
    return t('learning.warningTagsChange', { tags: warning.slice(TAGS_CHANGE_PREFIX.length) });
  }
  if (warning.startsWith(VERIFY_PREFIX)) {
    return localizeVerifyWarning(warning.slice(VERIFY_PREFIX.length), t);
  }
  if (warning.startsWith(LEGACY_VERIFY_PREFIX)) {
    // 코드 도입 전에 저장된 한글 문장 — 틀(warningVerify)만 로케일로, 본문은 원문 유지.
    return t('learning.warningVerify', { text: warning.slice(LEGACY_VERIFY_PREFIX.length) });
  }
  if (warning.endsWith(LEGACY_MASKED_SUFFIX)) {
    return t('learning.warningMasked', {
      label: warning.slice(0, -LEGACY_MASKED_SUFFIX.length),
    });
  }
  if (warning.startsWith(LEGACY_RISK_FLAG_PREFIX)) {
    return t('learning.warningRiskFlag', {
      label: warning.slice(LEGACY_RISK_FLAG_PREFIX.length),
    });
  }
  if (warning.startsWith(LEGACY_DESCRIPTION_CHANGE_PREFIX)) {
    return t('learning.warningDescriptionChange', {
      text: stripQuotes(warning.slice(LEGACY_DESCRIPTION_CHANGE_PREFIX.length)),
    });
  }
  if (warning.startsWith(LEGACY_TAGS_CHANGE_PREFIX)) {
    return t('learning.warningTagsChange', {
      tags: warning.slice(LEGACY_TAGS_CHANGE_PREFIX.length),
    });
  }
  return warning;
};
