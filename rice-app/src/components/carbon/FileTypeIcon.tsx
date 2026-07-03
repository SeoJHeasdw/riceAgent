import React from 'react';
import { fileIcon, type FileKind } from '../../lib/skillFiles';

/**
 * FileTypeIcon — language-tinted file glyphs for skill bundles (md·sh·py·html·
 * json·txt). Shared by the market file browser and the source workspace so a
 * skill's files read like an IDE (color carries the type at a glance).
 */

const DOC = 'M25.7 9.3l-7-7A1 1 0 0018 2H8a2 2 0 00-2 2v24a2 2 0 002 2h16a2 2 0 002-2V10a1 1 0 00-.3-.7zM18 4.4L23.6 10H18zM24 28H8V4h8v6a2 2 0 002 2h6z';
const WINDOW = 'M4 6v20h24V6zm22 18H6V12h20zm0-14H6V8h20z';

const PATHS: Record<FileKind, string> = {
  md: 'M26 4H6a2 2 0 00-2 2v20a2 2 0 002 2h20a2 2 0 002-2V6a2 2 0 00-2-2zM9 22V12l3 3 3-3v10h-2v-5l-1 1-1-1v5zm12-3l-3-3h2v-4h2v4h2z',
  sh: `${WINDOW}M9 16l3 3-3 3 1.4 1.4L13.8 19 9.4 14.6zM16 21h6v2h-6z`,
  py: `${WINDOW}M9 15h6v2H9zm0 4h10v2H9z`,
  html: `${WINDOW}M12 14.5 8.5 17l3.5 2.5 1.2-1.7L10.8 17l2.4-1.3zM20 14.5l-1.2 1.7L21.2 17l-2.4 1.3L20 19.5 23.5 17z`,
  json: DOC,
  txt: DOC,
  file: DOC,
};

const TINT: Record<FileKind, string> = {
  md: 'text-link-primary',
  sh: 'text-text-helper',
  py: 'text-support-success',
  html: 'text-orange-40',
  json: 'text-support-warning',
  txt: 'text-text-helper',
  file: 'text-icon-secondary',
};

export const FileTypeIcon: React.FC<{ path: string; size?: number; tinted?: boolean; className?: string }> = ({
  path,
  size = 15,
  tinted = true,
  className = '',
}) => {
  const kind = fileIcon(path);
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="currentColor" className={`${tinted ? TINT[kind] : ''} ${className}`}>
      <path d={PATHS[kind]} />
    </svg>
  );
};

export const FolderIcon: React.FC<{ size?: number; open?: boolean; className?: string }> = ({ size = 15, open = false, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="currentColor" className={className}>
    {open ? (
      <path d="M28 12H10.4l-2-2H4a2 2 0 00-2 2v14a2 2 0 002 2h21l5-12a2 2 0 00-2-2zM4 12h3.6l1.4 1.4.6.6H26v.5l-3.7 9H4z" />
    ) : (
      <path d="M11.2 6l3.4 3.4.6.6H28v16H4V6h7.2m.8-2H4a2 2 0 00-2 2v20a2 2 0 002 2h24a2 2 0 002-2V10a2 2 0 00-2-2H16l-3.4-3.4A2 2 0 0012 4z" />
    )}
  </svg>
);

// Made with Bob
