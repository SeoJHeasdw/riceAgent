import { useMemo, useRef, type MutableRefObject } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import type { CinematicPhase, SceneDragState } from '../sequences/types';

const seededRandom = (seed: number) => {
  let value = seed % 2147483647;
  return () => {
    value = (value * 16807) % 2147483647;
    return (value - 1) / 2147483646;
  };
};

export const StarField = ({ phase, drag }: { phase: CinematicPhase; drag: MutableRefObject<SceneDragState> }) => {
  const pointsRef = useRef<THREE.Points>(null);
  const positions = useMemo(() => {
    const random = seededRandom(8719);
    const values = new Float32Array(1100 * 3);
    for (let i = 0; i < 1100; i += 1) {
      const radius = 6 + random() * 18;
      const theta = random() * Math.PI * 2;
      const phi = Math.acos(2 * random() - 1);
      values[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
      values[i * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
      values[i * 3 + 2] = radius * Math.cos(phi) - 4;
    }
    return values;
  }, []);

  useFrame((state, delta) => {
    if (!pointsRef.current) return;
    if (phase === 'loop' && !drag.current.dragging) {
      drag.current.rotationY += drag.current.velocityY * delta * 60;
      drag.current.rotationX = THREE.MathUtils.clamp(
        drag.current.rotationX + drag.current.velocityX * delta * 60,
        -0.72,
        0.72,
      );
      drag.current.velocityX *= Math.pow(0.935, delta * 60);
      drag.current.velocityY *= Math.pow(0.935, delta * 60);
    }
    const targetY = state.clock.elapsedTime * 0.008 + (phase === 'loop' ? drag.current.rotationY : 0);
    const targetX = Math.sin(state.clock.elapsedTime * 0.05) * 0.03 + (phase === 'loop' ? drag.current.rotationX : 0);
    pointsRef.current.rotation.y = THREE.MathUtils.damp(pointsRef.current.rotation.y, targetY, 7, delta);
    pointsRef.current.rotation.x = THREE.MathUtils.damp(pointsRef.current.rotation.x, targetX, 7, delta);
  });

  return (
    <points ref={pointsRef} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        color="#78b7ff"
        size={0.027}
        sizeAttenuation
        transparent
        opacity={0.72}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
};
