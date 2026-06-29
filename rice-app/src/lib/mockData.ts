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
  maxTokens: 2048,
  contextLength: '32K',
  registeredModels: [],
  endpoint: 'http://localhost:11434',
  userModelingEnabled: true,
  localStorageOnly: false,
};

// Skill categories
export const skillCategories = [
  'All',
  '데이터 분석',
  '자동화',
  '연동',
  '개발',
  '커뮤니케이션',
  '생산성',
];

// 플러그인 카테고리
export const pluginCategories = [
  'WXO',
  '영업',
  'HR',
  '재무',
  '마케팅',
  'IT 운영',
];

// Made with Bob
