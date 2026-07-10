export type RiceTheme = 'dark' | 'light';
export type RiceFont = 'system' | 'mono' | 'sans';

export interface RiceAppearance {
  theme?: RiceTheme;
  font?: RiceFont;
}

export const APPEARANCE_STORAGE_KEY = 'rice.settings';

/** Apply appearance at document level so every route and overlay stays in sync. */
export function applyAppearance({ theme = 'dark', font = 'system' }: RiceAppearance) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.dataset.font = font;
  root.style.colorScheme = theme;
}

/** Runs before React mounts to avoid a theme/font flash on startup. */
export function applyStoredAppearance() {
  try {
    const raw = localStorage.getItem(APPEARANCE_STORAGE_KEY);
    const stored = raw ? (JSON.parse(raw) as RiceAppearance) : {};
    applyAppearance(stored);
  } catch {
    applyAppearance({ theme: 'dark', font: 'system' });
  }
}

