export type CinematicPhase = 'awakening' | 'loop' | 'ecosystem' | 'model' | 'handoff';

export type AwakeningBeat = 'void' | 'assembly' | 'awake';

/** Mutable interaction state shared with the render loop without React re-renders. */
export interface SceneDragState {
  dragging: boolean;
  lastX: number;
  lastY: number;
  rotationX: number;
  rotationY: number;
  velocityX: number;
  velocityY: number;
}

export interface CapabilitySpec {
  id: 'skill' | 'connector' | 'plugin';
  eyebrow: string;
  title: string;
  description: string;
  metric: string;
  accent: string;
}
