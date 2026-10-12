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

/**
 * A quiet, authored calibration field behind the core.
 *
 * Short strokes sit on three loose technical grids instead of filling space
 * with glowing points. The field keeps the original drag inertia and depth
 * response, while reading as product instrumentation rather than a star sky.
 */
export const SignalField = ({
  phase,
  drag,
  reducedMotion,
  lowPower,
}: {
  phase: CinematicPhase;
  drag: MutableRefObject<SceneDragState>;
  reducedMotion: boolean;
  lowPower: boolean;
}) => {
  const fieldRef = useRef<THREE.LineSegments>(null);
  const geometry = useMemo(() => {
    const random = seededRandom(8719);
    const positions: number[] = [];
    const colors: number[] = [];
    const graphite = new THREE.Color('#78889a');
    const blue = new THREE.Color('#7394b8');
    const violet = new THREE.Color('#8d819d');
    const teal = new THREE.Color('#718f8c');

    const layers = lowPower ? 2 : 3;
    for (let layer = 0; layer < layers; layer += 1) {
      const columns = lowPower ? 11 : 15;
      const rows = lowPower ? 7 : 9;
      for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
          // The deliberate gaps keep the pattern airy and non-repetitive.
          if (random() < 0.38) continue;

          const x = (column - (columns - 1) / 2) * (1.24 + layer * 0.14) + (random() - 0.5) * 0.14;
          const y = (row - (rows - 1) / 2) * (0.9 + layer * 0.08) + (random() - 0.5) * 0.1;
          const z = -4.8 - layer * 4.8 + (random() - 0.5) * 0.5;
          const length = 0.1 + random() * (column % 5 === 0 ? 0.28 : 0.16);
          const vertical = (row + column + layer) % 7 === 0;

          if (vertical) {
            positions.push(x, y - length / 2, z, x, y + length / 2, z);
          } else {
            positions.push(x - length / 2, y, z, x + length / 2, y, z);
          }

          const tint = (row * columns + column + layer * 11) % 29 === 0
            ? teal
            : (row + column * 3 + layer) % 23 === 0
              ? violet
              : (column + layer) % 9 === 0
                ? blue
                : graphite;
          colors.push(tint.r, tint.g, tint.b, tint.r, tint.g, tint.b);
        }
      }
    }

    const next = new THREE.BufferGeometry();
    next.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    next.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    return next;
  }, [lowPower]);

  useFrame((state, delta) => {
    const field = fieldRef.current;
    if (!field) return;

    const interactive = phase === 'loop' || phase === 'ecosystem' || phase === 'model';
    if (!reducedMotion && interactive && !drag.current.dragging) {
      drag.current.rotationY += drag.current.velocityY * delta * 60;
      drag.current.rotationX = THREE.MathUtils.clamp(
        drag.current.rotationX + drag.current.velocityX * delta * 60,
        -0.72,
        0.72,
      );
      drag.current.velocityX *= Math.pow(0.935, delta * 60);
      drag.current.velocityY *= Math.pow(0.935, delta * 60);
    }

    const sceneRotationY = reducedMotion ? 0 : drag.current.rotationY * 0.3;
    const sceneRotationX = reducedMotion ? 0 : drag.current.rotationX * 0.2;
    const targetY = reducedMotion ? 0 : state.clock.elapsedTime * 0.0015 + sceneRotationY;
    const targetX = reducedMotion ? 0 : Math.sin(state.clock.elapsedTime * 0.045) * 0.01 + sceneRotationX;
    field.rotation.y = THREE.MathUtils.damp(field.rotation.y, targetY, 6, delta);
    field.rotation.x = THREE.MathUtils.damp(field.rotation.x, targetX, 6, delta);
    field.position.y = reducedMotion ? 0 : Math.sin(state.clock.elapsedTime * 0.12) * 0.03;
  });

  return (
    <lineSegments ref={fieldRef} geometry={geometry} frustumCulled={false}>
      <lineBasicMaterial
        vertexColors
        transparent
        opacity={0.34}
        depthWrite={false}
        toneMapped={false}
      />
    </lineSegments>
  );
};
