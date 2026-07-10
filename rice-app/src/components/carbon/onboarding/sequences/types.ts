export type CinematicPhase = 'awakening' | 'loop' | 'ecosystem' | 'model' | 'handoff';

export type AwakeningBeat = 'void' | 'assembly' | 'awake';

export interface CapabilitySpec {
  id: 'skill' | 'connector' | 'plugin';
  eyebrow: string;
  title: string;
  description: string;
  metric: string;
  accent: string;
}

