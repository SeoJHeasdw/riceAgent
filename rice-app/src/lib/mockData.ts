import { ModelSettings } from '../components/workspace/ModelSettingsPanel';

/**
 * Sample marketplace data for the chat surface.
 * Models are NOT listed here: SaaS models are user-registered (model + API key)
 * and local models are fetched live from Ollama. (Learning-loop content lives in
 * ./learningData.)
 */

// Default model settings (used until the runtime restores saved settings).
// temperature: 백엔드 ProviderConfig 기본(0.5)과 일치 — 도구 인자 JSON 정확도와
// 안정성의 균형. 구조화 생성/편집 전용 호출은 이 값과 무관하게 런타임이 0.1로
// 고정한다. 창작 요청만 사용자가 올린다.
export const defaultModelSettings: ModelSettings = {
  temperature: 0.5,
  registeredModels: [],
  endpoint: 'http://localhost:11434',
  userModelingEnabled: true,
  localStorageOnly: false,
  showThinking: true,
};

/**
 * Skill categories — stable codes used in SkillMarketScreen (i18n-ready).
 * The first entry 'all' is the "show all" sentinel.
 * Each code maps to skills:market.category<PascalCase> in the i18n catalog.
 */
export const skillCategoryCodes = [
  'all',
  'dataAnalysis',
  'automation',
  'integration',
  'dev',
  'communication',
  'productivity',
] as const;
export type SkillCategoryCode = (typeof skillCategoryCodes)[number];

/**
 * Plugin categories — stable codes used in PluginMarketScreen (i18n-ready).
 * Rendered via t('plugins:category.<code>').
 */
export const pluginCategoryCodes = [
  'sales',
  'hr',
  'finance',
  'marketing',
  'itOps',
] as const;
export type PluginCategoryCode = (typeof pluginCategoryCodes)[number];
