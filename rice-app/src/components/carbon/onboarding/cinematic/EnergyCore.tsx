import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import type { AwakeningBeat, CinematicPhase } from '../sequences/types';

interface EnergyCoreProps {
  phase: CinematicPhase;
  awakeningBeat: AwakeningBeat;
}

const RINGS = [
  { radius: 1.42, tube: 0.018, arc: 1.58, tilt: [1.18, 0.22, 0.1], speed: 0.12, delay: 0.18 },
  { radius: 1.66, tube: 0.011, arc: 1.2, tilt: [0.86, -0.34, 1.1], speed: -0.08, delay: 0.48 },
  { radius: 1.9, tube: 0.014, arc: 1.72, tilt: [1.52, 0.1, -0.45], speed: 0.055, delay: 0.72 },
] as const;

const easeOutExpo = (value: number) => (value >= 1 ? 1 : 1 - 2 ** (-10 * value));

const HudRing = ({ spec, index }: { spec: (typeof RINGS)[number]; index: number }) => {
  const ref = useRef<THREE.Group>(null);

  useFrame((state, delta) => {
    const group = ref.current;
    if (!group) return;
    const progress = THREE.MathUtils.clamp((state.clock.elapsedTime - spec.delay) / 1.5, 0, 1);
    const eased = easeOutExpo(progress);
    group.scale.setScalar(Math.max(eased, 0.001));
    group.rotation.z += delta * spec.speed;
    group.rotation.y += delta * spec.speed * 0.25;
  });

  return (
    <group ref={ref} rotation={spec.tilt}>
      <mesh rotation={[0, 0, index * 1.75]}>
        <torusGeometry args={[spec.radius, spec.tube, 8, 128, Math.PI * spec.arc]} />
        <meshBasicMaterial
          color={index === 1 ? '#9c7cff' : '#5fa8ff'}
          transparent
          opacity={0.72 - index * 0.12}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
      {Array.from({ length: index === 2 ? 24 : 12 }, (_, marker) => {
        const angle = (marker / (index === 2 ? 24 : 12)) * Math.PI * 2;
        return (
          <mesh
            key={marker}
            position={[Math.cos(angle) * spec.radius, Math.sin(angle) * spec.radius, 0]}
            rotation={[0, 0, angle]}
          >
            <boxGeometry args={[index === 2 ? 0.045 : 0.025, index === 2 ? 0.12 : 0.065, 0.012]} />
            <meshBasicMaterial color="#86c8ff" transparent opacity={marker % 3 === 0 ? 0.7 : 0.28} />
          </mesh>
        );
      })}
    </group>
  );
};

export const EnergyCore = ({ phase, awakeningBeat }: EnergyCoreProps) => {
  const groupRef = useRef<THREE.Group>(null);
  const shellRef = useRef<THREE.Mesh>(null);
  const pulseRef = useRef<THREE.Mesh>(null);
  const phaseScale = phase === 'handoff' ? 1.24 : phase === 'model' ? 0.84 : 1;
  const fragments = useMemo(
    () =>
      Array.from({ length: 18 }, (_, index) => ({
        angle: (index / 18) * Math.PI * 2,
        radius: 1.18 + (index % 3) * 0.08,
        z: ((index % 5) - 2) * 0.085,
      })),
    [],
  );

  useFrame((state, delta) => {
    const group = groupRef.current;
    if (!group) return;
    const introScale = awakeningBeat === 'void' ? 0.02 : awakeningBeat === 'assembly' ? 0.88 : 1;
    const targetScale = introScale * phaseScale;
    const next = THREE.MathUtils.damp(group.scale.x, targetScale, 3.2, delta);
    group.scale.setScalar(next);
    group.rotation.y += delta * (phase === 'handoff' ? 0.2 : 0.055);
    group.rotation.x = THREE.MathUtils.damp(group.rotation.x, state.pointer.y * 0.08, 2.4, delta);
    group.rotation.z = THREE.MathUtils.damp(group.rotation.z, -state.pointer.x * 0.05, 2.4, delta);

    if (shellRef.current) {
      shellRef.current.rotation.y -= delta * 0.09;
      shellRef.current.rotation.z += delta * 0.035;
    }
    if (pulseRef.current) {
      const pulse = 1 + Math.sin(state.clock.elapsedTime * 2.1) * 0.055;
      pulseRef.current.scale.setScalar(pulse);
    }
  });

  return (
    <group ref={groupRef}>
      <pointLight color="#3a8dff" intensity={8} distance={8} decay={2.2} />
      <pointLight position={[0.8, -0.6, 1.5]} color="#9d70ff" intensity={5} distance={5} />

      <mesh ref={pulseRef}>
        <sphereGeometry args={[0.63, 64, 64]} />
        <meshPhysicalMaterial
          color="#0b4dba"
          emissive="#1677ff"
          emissiveIntensity={2.5}
          metalness={0.4}
          roughness={0.16}
          clearcoat={1}
          clearcoatRoughness={0.08}
        />
      </mesh>
      <mesh scale={0.77}>
        <icosahedronGeometry args={[0.72, 3]} />
        <meshBasicMaterial color="#bce9ff" wireframe transparent opacity={0.32} blending={THREE.AdditiveBlending} />
      </mesh>
      <mesh ref={shellRef}>
        <icosahedronGeometry args={[1.02, 3]} />
        <meshPhysicalMaterial
          color="#061326"
          emissive="#0a4da8"
          emissiveIntensity={0.7}
          metalness={0.88}
          roughness={0.2}
          transparent
          opacity={0.84}
          wireframe
        />
      </mesh>
      <mesh scale={1.06}>
        <sphereGeometry args={[1, 48, 48]} />
        <meshBasicMaterial
          color="#2d8dff"
          side={THREE.BackSide}
          transparent
          opacity={0.075}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>

      {fragments.map((fragment, index) => (
        <mesh
          key={index}
          position={[
            Math.cos(fragment.angle) * fragment.radius,
            Math.sin(fragment.angle) * fragment.radius,
            fragment.z,
          ]}
          rotation={[0, 0, fragment.angle]}
        >
          <boxGeometry args={[0.025, index % 4 === 0 ? 0.2 : 0.1, 0.018]} />
          <meshBasicMaterial color={index % 5 === 0 ? '#b69aff' : '#7fc9ff'} transparent opacity={0.66} />
        </mesh>
      ))}

      {RINGS.map((ring, index) => (
        <HudRing key={ring.radius} spec={ring} index={index} />
      ))}
    </group>
  );
};

