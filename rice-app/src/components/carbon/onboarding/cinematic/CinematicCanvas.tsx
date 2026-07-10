import { Canvas } from '@react-three/fiber';
import * as THREE from 'three';
import type { AwakeningBeat, CinematicPhase } from '../sequences/types';
import { CoreScene } from './CoreScene';

interface CinematicCanvasProps {
  phase: CinematicPhase;
  awakeningBeat: AwakeningBeat;
  capabilityIndex: number;
}

export const CinematicCanvas = ({ phase, awakeningBeat, capabilityIndex }: CinematicCanvasProps) => (
  <Canvas
    className="!absolute inset-0"
    camera={{ position: [0, 0, 7.5], fov: 42, near: 0.1, far: 80 }}
    dpr={[1, 1.55]}
    gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
    onCreated={({ gl }) => {
      gl.toneMapping = THREE.ACESFilmicToneMapping;
      gl.toneMappingExposure = 1.18;
      gl.setClearColor('#010309', 0);
    }}
    fallback={<div className="cinematic-core-fallback" aria-hidden="true" />}
  >
    <CoreScene phase={phase} awakeningBeat={awakeningBeat} capabilityIndex={capabilityIndex} />
  </Canvas>
);

