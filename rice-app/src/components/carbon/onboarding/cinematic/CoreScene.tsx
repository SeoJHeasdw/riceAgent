import { useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import type { AwakeningBeat, CinematicPhase } from '../sequences/types';
import { CapabilityOrbit } from './CapabilityOrbit';
import { EnergyCore } from './EnergyCore';
import { StarField } from './StarField';

interface CoreSceneProps {
  phase: CinematicPhase;
  awakeningBeat: AwakeningBeat;
  capabilityIndex: number;
}

export const CoreScene = ({ phase, awakeningBeat, capabilityIndex }: CoreSceneProps) => {
  const rigRef = useRef<THREE.Group>(null);
  const { viewport } = useThree();

  useFrame((state, delta) => {
    const rig = rigRef.current;
    if (!rig) return;
    const compact = viewport.width < 7.4;
    let targetX = 0;
    let targetY = 0;
    if (!compact && phase === 'ecosystem') targetX = 1.45;
    if (!compact && phase === 'model') targetX = -2.45;
    if (compact && phase === 'ecosystem') targetY = 0.65;
    if (compact && phase === 'model') targetY = 1.65;
    rig.position.x = THREE.MathUtils.damp(rig.position.x, targetX, 3.2, delta);
    rig.position.y = THREE.MathUtils.damp(rig.position.y, targetY, 3.2, delta);
    rig.rotation.y = THREE.MathUtils.damp(rig.rotation.y, state.pointer.x * 0.06, 2, delta);
  });

  return (
    <>
      <fog attach="fog" args={['#010309', 8, 27]} />
      <ambientLight intensity={0.28} color="#7db6ff" />
      <directionalLight position={[3, 4, 6]} intensity={1.1} color="#bfdcff" />
      <StarField />
      <group ref={rigRef}>
        <EnergyCore phase={phase} awakeningBeat={awakeningBeat} />
        <CapabilityOrbit phase={phase} capabilityIndex={capabilityIndex} />
      </group>
    </>
  );
};

