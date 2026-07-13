import { ModelSettings } from '../components/carbon/ModelSettingsPanel';

/**
 * Sample marketplace data for the chat surface.
 * Models are NOT listed here: SaaS models are user-registered (model + API key)
 * and local models are fetched live from Ollama. (Learning-loop content lives in
 * ./learningData.)
 */

// Default model settings (used until the runtime restores saved settings)
export const defaultModelSettings: ModelSettings = {
  temperature: 0.7,
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
  'WXO',
  'sales',
  'hr',
  'finance',
  'marketing',
  'itOps',
] as const;
export type PluginCategoryCode = (typeof pluginCategoryCodes)[number];

// Made with Bob
