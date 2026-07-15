import React from 'react';

/**
 * Icon - lightweight inline icon set (feather-style, 24px stroke).
 * A small, dependency-free subset of the glyphs the learning-loop surfaces
 * need. Stroke-based by default; "filled" status glyphs (check/warning) take
 * a color via className.
 */

export type IconName =
  | 'renew'
  | 'plug'
  | 'user'
  | 'skill'
  | 'growth'
  | 'overflow'
  | 'edit'
  | 'close'
  | 'trash'
  | 'view'
  | 'view-off'
  | 'chevron-down'
  | 'chevron-up'
  | 'check'
  | 'check-circle'
  | 'warning'
  | 'copy'
  | 'thumbs-up'
  | 'thumbs-down'
  | 'bookmark'
  | 'sparkle'
  | 'idea'
  | 'arrow-up-right'
  | 'arrow-right'
  | 'share'
  | 'export'
  | 'download'
  | 'add'
  | 'lock'
  | 'send'
  | 'attachment'
  | 'microphone'
  | 'search'
  | 'settings'
  | 'cloud'
  | 'calendar'
  | 'server'
  | 'connect'
  | 'chat'
  | 'flow'
  | 'merge'
  | 'database'
  | 'rule'
  | 'tools'
  | 'model'
  | 'radio'
  | 'radio-checked'
  | 'terminal'
  | 'document'
  | 'folder'
  | 'side-panel-close'
  | 'side-panel-open'
  | 'sidebar'
  | 'compose';

const PATHS: Record<IconName, React.ReactNode> = {
  renew: <><path d="M3 12a9 9 0 0 1 15-6.7L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" /><path d="M3 21v-5h5" /></>,
  plug: <><path d="M9 2v6M15 2v6" /><path d="M6 8h12v3a6 6 0 0 1-12 0V8z" /><path d="M12 17v5" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  skill: <><circle cx="6" cy="9" r="2.4" /><circle cx="18" cy="15" r="2.4" /><path d="M8.3 9h5.4M10.3 15h5.4" /><path d="M13.7 9 16 12M10.3 15 8 12" /></>,
  growth: <><path d="M3 17l6-6 4 4 7-7" /><path d="M17 8h4v4" /></>,
  overflow: <><circle cx="12" cy="5" r="1.1" /><circle cx="12" cy="12" r="1.1" /><circle cx="12" cy="19" r="1.1" /></>,
  edit: <><path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" /></>,
  close: <><path d="M18 6 6 18M6 6l12 12" /></>,
  trash: <><path d="M3 6h18" /><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6M14 11v6" /></>,
  view: <><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" /><circle cx="12" cy="12" r="3" /></>,
  'view-off': <><path d="M17.9 17.9A10.6 10.6 0 0 1 12 19c-7 0-11-7-11-7a18.5 18.5 0 0 1 5.1-5.9M9.9 4.2A10.9 10.9 0 0 1 12 4c7 0 11 7 11 7a18.6 18.6 0 0 1-2.2 3.2m-6.7-1.1a3 3 0 1 1-4.2-4.2" /><path d="m1 1 22 22" /></>,
  'chevron-down': <><path d="m6 9 6 6 6-6" /></>,
  'chevron-up': <><path d="m6 15 6-6 6 6" /></>,
  check: <><path d="M20 6 9 17l-5-5" /></>,
  'check-circle': <><circle cx="12" cy="12" r="9" /><path d="m8.5 12.5 2.5 2.5 4.5-5" /></>,
  warning: <><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" /></>,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></>,
  'thumbs-up': <><path d="M7 10v11M2 11h5v10H2z" /><path d="M7 10l4-7a2 2 0 0 1 3 1.7V8h5a2 2 0 0 1 2 2.3l-1.3 7A2 2 0 0 1 16.7 19H7" /></>,
  'thumbs-down': <><path d="M17 14V3M22 13h-5V3h5z" /><path d="M17 14l-4 7a2 2 0 0 1-3-1.7V16H5a2 2 0 0 1-2-2.3l1.3-7A2 2 0 0 1 7.3 5H17" /></>,
  bookmark: <><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" /></>,
  // 학습/증류 액션 전용 — "이 대화를 스킬로 저장" (bookmark 대체, Self-Improvement v3)
  sparkle: <><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" /><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z" /></>,
  idea: <><path d="M9 18h6" /><path d="M10 21h4" /><path d="M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.2 1 2.5h6c0-1.3.3-1.8 1-2.5A6 6 0 0 0 12 3z" /></>,
  'arrow-up-right': <><path d="M7 17 17 7M8 7h9v9" /></>,
  'arrow-right': <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  share: <><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4" /></>,
  export: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 9l5-5 5 5M12 4v12" /></>,
  download: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5M12 15V3" /></>,
  add: <><path d="M12 5v14M5 12h14" /></>,
  lock: <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>,
  send: <><path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" /></>,
  attachment: <><path d="M21.4 11.05 12.25 20.2a5 5 0 0 1-7.07-7.07l9.19-9.19a3 3 0 0 1 4.24 4.24l-9.2 9.19a1 1 0 0 1-1.41-1.41l8.48-8.49" /></>,
  microphone: <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>,
  cloud: <><path d="M17.5 19a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.6-1.5A4 4 0 0 0 6.5 19z" /></>,
  calendar: <><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></>,
  server: <><rect x="3" y="4" width="18" height="7" rx="1" /><rect x="3" y="13" width="18" height="7" rx="1" /><path d="M7 7.5h.01M7 16.5h.01" /></>,
  connect: <><path d="M10 13a5 5 0 0 0 7 0l2.5-2.5a5 5 0 0 0-7-7L11 5" /><path d="M14 11a5 5 0 0 0-7 0l-2.5 2.5a5 5 0 0 0 7 7L13 19" /></>,
  chat: <><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.2A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z" /></>,
  flow: <><rect x="3" y="3" width="6" height="6" rx="1" /><rect x="15" y="15" width="6" height="6" rx="1" /><path d="M9 6h6a3 3 0 0 1 3 3v6" /></>,
  // Consolidation — two independent skill nodes converge into one result.
  merge: <><circle cx="7" cy="5" r="2" /><circle cx="17" cy="5" r="2" /><circle cx="12" cy="19" r="2" /><path d="M7 7v1.5a3.5 3.5 0 0 0 3.5 3.5H12v5" /><path d="M17 7v1.5a3.5 3.5 0 0 1-3.5 3.5H12" /></>,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>,
  rule: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h4M8 12h8M8 16h6" /></>,
  tools: <><path d="M14.7 6.3a4 4 0 0 1 5.3 5.3l-1.4-1.4-2.6 2.6-2.5-2.5 2.6-2.6z" /><path d="M13 11 4.5 19.5a2.1 2.1 0 0 1-3-3L10 8" /></>,
  model: <><circle cx="12" cy="12" r="3" /><circle cx="12" cy="4" r="1.6" /><circle cx="12" cy="20" r="1.6" /><circle cx="4" cy="12" r="1.6" /><circle cx="20" cy="12" r="1.6" /><path d="M12 9V5.6M12 18.4V15M9 12H5.6M18.4 12H15" /></>,
  radio: <><circle cx="12" cy="12" r="9" /></>,
  'radio-checked': <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none" /></>,
  terminal: <><rect x="2" y="4" width="20" height="16" rx="2" /><path d="m7 9 3 3-3 3M13 15h4" /></>,
  document: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></>,
  folder: <><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H10l2 2h6.5A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z" /><path d="M3 9h18" /></>,
  'side-panel-close': <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /><path d="M11.5 9.5 9 12l2.5 2.5" /></>,
  'side-panel-open': <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /><path d="M8.5 9.5 11 12l-2.5 2.5" /></>,
  // Sidebar toggle — clean panel glyph (no chevron); the fixed chat-list toggle by the traffic lights
  sidebar: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>,
  // Compose / new chat — rounded square with a pencil (ChatGPT/Codex "새 대화" affordance)
  compose: <><path d="M11 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L13 14l-4 1 1-4z" /></>,
};

export interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
  /** Use filled rendering (currentColor fill, no stroke) for status glyphs */
  filled?: boolean;
  style?: React.CSSProperties;
  title?: string;
}

export const Icon: React.FC<IconProps> = ({ name, size = 16, className = '', filled = false, style, title }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill={filled ? 'currentColor' : 'none'}
    stroke={filled ? 'none' : 'currentColor'}
    strokeWidth={1.8}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    style={style}
    aria-hidden={title ? undefined : true}
    role={title ? 'img' : undefined}
  >
    {title && <title>{title}</title>}
    {PATHS[name]}
  </svg>
);

// Made with Bob
