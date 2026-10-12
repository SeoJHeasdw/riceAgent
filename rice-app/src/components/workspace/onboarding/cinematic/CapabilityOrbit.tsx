import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import type { CinematicPhase } from '../sequences/types';

interface CapabilityOrbitProps {
  phase: CinematicPhase;
  capabilityIndex: number;
  reducedMotion: boolean;
  lowPower: boolean;
}

const SPECS = [
  { color: '#5b8cff', angle: 0.4, speed: 0.17, shape: 'skill' },
  { color: '#36d8c4', angle: 2.55, speed: -0.12, shape: 'connector' },
  { color: '#aa7dff', angle: 4.55, speed: 0.09, shape: 'plugin' },
] as const;

const Satellite = ({
  spec,
  visible,
  reducedMotion,
  lowPower,
}: {
  spec: (typeof SPECS)[number];
  visible: boolean;
  reducedMotion: boolean;
  lowPower: boolean;
}) => {
  const ref = useRef<THREE.Group>(null);
  useFrame((state, delta) => {
    const group = ref.current;
    if (!group) return;
    const target = visible ? 1 : 0.001;
    const scale = reducedMotion ? target : THREE.MathUtils.damp(group.scale.x, target, 5.5, delta);
    group.scale.setScalar(scale);
    if (!reducedMotion) {
      group.rotation.x += delta * 0.35;
      group.rotation.y += delta * 0.5;
    }
    group.position.z = reducedMotion ? 0 : Math.sin(state.clock.elapsedTime * 0.8 + spec.angle) * 0.18;
  });

  return (
    <group
      ref={ref}
      position={[Math.cos(spec.angle) * 2.65, Math.sin(spec.angle) * 1.86, 0]}
      scale={0.001}
    >
      <pointLight color={spec.color} intensity={3.5} distance={2.6} />
      <mesh>
        {spec.shape === 'skill' && <octahedronGeometry args={[0.25, 1]} />}
        {spec.shape === 'connector' && <torusGeometry args={[0.22, 0.075, 12, 36]} />}
        {spec.shape === 'plugin' && <dodecahedronGeometry args={[0.25, 0]} />}
        <meshPhysicalMaterial
          color={spec.color}
          emissive={spec.color}
          emissiveIntensity={1.25}
          metalness={0.75}
          roughness={0.22}
        />
      </mesh>
      <mesh scale={1.38}>
        <sphereGeometry args={[0.27, lowPower ? 12 : 24, lowPower ? 12 : 24]} />
        <meshBasicMaterial color={spec.color} transparent opacity={0.08} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
    </group>
  );
};

export const CapabilityOrbit = ({
  phase,
  capabilityIndex,
  reducedMotion,
  lowPower,
}: CapabilityOrbitProps) => {
  const groupRef = useRef<THREE.Group>(null);
  const enabled = phase === 'ecosystem' || phase === 'model' || phase === 'handoff';

  useFrame((_, delta) => {
    if (!groupRef.current) return;
    if (!reducedMotion) groupRef.current.rotation.z += delta * 0.018;
  });

  return (
    <group ref={groupRef}>
      <mesh rotation={[1.34, 0.08, 0.18]} visible={enabled}>
        <torusGeometry args={[2.45, 0.006, 6, lowPower ? 96 : 180]} />
        <meshBasicMaterial color="#5d86bd" transparent opacity={0.24} />
      </mesh>
      {SPECS.map((spec, index) => (
        <Satellite
          key={spec.shape}
          spec={spec}
          visible={enabled && (phase !== 'ecosystem' || index <= capabilityIndex)}
          reducedMotion={reducedMotion}
          lowPower={lowPower}
        />
      ))}
    </group>
  );
};
