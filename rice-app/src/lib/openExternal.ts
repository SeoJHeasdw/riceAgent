/**
 * Open an http(s) URL in the user's default browser.
 *
 * In Electron this routes through the main process (`shell.openExternal` — see
 * preload `app.openExternal`) so the OS browser opens, not an in-app window
 * (OAuth consent must run in the real browser — CONTRACTS §4.2 step 3). In a
 * plain browser (renderer-only dev) it falls back to `window.open`.
 */
type OpenExternalBridge = { app?: { openExternal?: (url: string) => Promise<boolean> } };

export async function openExternal(url: string): Promise<void> {
  const bridge = (window as unknown as { electronAPI?: OpenExternalBridge }).electronAPI?.app;
  if (bridge?.openExternal) {
    try {
      await bridge.openExternal(url);
      return;
    } catch {
      /* fall through to window.open */
    }
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}
