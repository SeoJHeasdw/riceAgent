import { Skill, SkillFile, SkillFileLanguage } from './types';

/** kebab-case slug from a skill name */
const slugify = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '');

export type FileKind = 'md' | 'sh' | 'py' | 'html' | 'json' | 'txt' | 'file';

const fileIcon = (path: string): FileKind => {
  if (path.endsWith('.md')) return 'md';
  if (path.endsWith('.sh')) return 'sh';
  if (path.endsWith('.py')) return 'py';
  if (path.endsWith('.html') || path.endsWith('.htm')) return 'html';
  if (path.endsWith('.json')) return 'json';
  if (path.endsWith('.txt')) return 'txt';
  return 'file';
};

/** 확장자 → 구문 강조용 언어. 파일 생성·편집 시 언어 추론을 한 곳에서 결정한다. */
export const languageFromPath = (path: string): SkillFileLanguage => {
  if (path.endsWith('.py')) return 'python';
  if (path.endsWith('.sh')) return 'shell';
  if (path.endsWith('.html') || path.endsWith('.htm')) return 'html';
  if (path.endsWith('.json')) return 'json';
  if (path.endsWith('.md')) return 'markdown';
  return 'text';
};

/**
 * Returns the files bundled with a skill. When a skill doesn't ship explicit
 * files, we synthesize a realistic SKILL.md / README / script / license set
 * from its metadata so the detail modal always has something to show.
 */
export function getSkillFiles(skill: Skill): SkillFile[] {
  if (skill.files && skill.files.length) return skill.files;

  const slug = slugify(skill.name);
  const cmd = `/${slug.replace(/-/g, '')}`;
  const primaryTag = skill.tags[0] ?? 'general';
  const by = `${skill.author}${skill.team ? ` · ${skill.team}` : ''}`;

  const skillMd: SkillFile = {
    path: 'SKILL.md',
    language: 'markdown',
    content: `---
name: ${slug}
description: ${skill.description}
version: ${skill.version}
---

# ${skill.name}

${skill.description}

## When to use

Use this skill for **${primaryTag}** tasks. RICE can invoke it automatically
when a request is relevant, or you can call it directly in chat with \`${cmd}\`.

## How it works

1. Collect the inputs required for ${skill.name}.
2. Run \`scripts/run.sh\` to execute the workflow.
3. Review and refine the generated output.

## Tags

${skill.tags.map((t) => `- ${t}`).join('\n')}
`,
  };

  const readme: SkillFile = {
    path: 'README.md',
    language: 'markdown',
    content: `# ${skill.name}

> ${skill.description}

Maintained by ${by}.

## Installation

Install from the Skill market, then call \`${cmd}\` in chat or let RICE pick it
up automatically.

## Files

- \`SKILL.md\` — skill definition and usage instructions
- \`scripts/run.sh\` — entry point that runs the workflow
- \`LICENSE.txt\` — license terms

## Support

Questions or issues? Reach out to ${skill.author}.
`,
  };

  const runScript: SkillFile = {
    path: 'scripts/run.sh',
    language: 'shell',
    content: `#!/usr/bin/env bash
set -euo pipefail

# ${skill.name} — ${skill.description}
# Version ${skill.version}

echo "Running ${skill.name} (${primaryTag})…"

# TODO: implement the ${primaryTag} workflow here
`,
  };

  const license: SkillFile = {
    path: 'LICENSE.txt',
    language: 'text',
    content: `MIT License

Copyright (c) ${new Date().getFullYear()} ${skill.author}

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files, to deal in the software
without restriction, including without limitation the rights to use, copy,
modify, merge, publish, distribute, sublicense, and/or sell copies of the
software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
`,
  };

  return [skillMd, readme, runScript, license];
}

export { fileIcon };
