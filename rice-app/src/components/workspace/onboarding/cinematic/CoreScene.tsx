import { useRef, type MutableRefObject } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import type { AwakeningBeat, CinematicPhase, SceneDragState } from '../sequences/types';
import { CapabilityOrbit } from './CapabilityOrbit';
import { EnergyCore } from './EnergyCore';
import { SignalField } from './SignalField';

interface CoreSceneProps {
  phase: CinematicPhase;
  awakeningBeat: AwakeningBeat;
  capabilityIndex: number;
  drag: MutableRefObject<SceneDragState>;
  reducedMotion: boolean;
  lowPower: boolean;
}

export const CoreScene = ({
  phase,
  awakeningBeat,
  capabilityIndex,
  drag,
  reducedMotion,
  lowPower,
}: CoreSceneProps) => {
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
    const motionPointerX = reducedMotion ? 0 : state.pointer.x;
    const motionPointerY = reducedMotion ? 0 : state.pointer.y;
    const response = reducedMotion ? 100 : 3.2;
    rig.position.x = THREE.MathUtils.damp(rig.position.x, targetX, response, delta);
    rig.position.y = THREE.MathUtils.damp(rig.position.y, targetY, response, delta);
    const dragRotationY = reducedMotion ? 0 : drag.current.rotationY * 0.11;
    const dragRotationX = reducedMotion ? 0 : drag.current.rotationX * 0.08;
    rig.rotation.y = THREE.MathUtils.damp(rig.rotation.y, motionPointerX * 0.045 + dragRotationY, 2.4, delta);
    rig.rotation.x = THREE.MathUtils.damp(rig.rotation.x, -motionPointerY * 0.025 + dragRotationX, 2.4, delta);
  });

  return (
    <>
      <fog attach="fog" args={['#101319', 9, 30]} />
      <ambientLight intensity={0.34} color="#9fb7d1" />
      <directionalLight position={[3, 4, 6]} intensity={1.1} color="#bfdcff" />
      <SignalField phase={phase} drag={drag} reducedMotion={reducedMotion} lowPower={lowPower} />
      <group ref={rigRef}>
        <EnergyCore phase={phase} awakeningBeat={awakeningBeat} reducedMotion={reducedMotion} lowPower={lowPower} />
        <CapabilityOrbit phase={phase} capabilityIndex={capabilityIndex} reducedMotion={reducedMotion} lowPower={lowPower} />
      </group>
    </>
  );
};
