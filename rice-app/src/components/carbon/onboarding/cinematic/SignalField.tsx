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
}: {
  phase: CinematicPhase;
  drag: MutableRefObject<SceneDragState>;
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

    for (let layer = 0; layer < 3; layer += 1) {
      const columns = 15;
      const rows = 9;
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
  }, []);

  useFrame((state, delta) => {
    const field = fieldRef.current;
    if (!field) return;

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

    const loopRotationY = phase === 'loop' ? drag.current.rotationY * 0.34 : 0;
    const loopRotationX = phase === 'loop' ? drag.current.rotationX * 0.22 : 0;
    const targetY = state.clock.elapsedTime * 0.0018 + loopRotationY;
    const targetX = Math.sin(state.clock.elapsedTime * 0.045) * 0.012 + loopRotationX;
    field.rotation.y = THREE.MathUtils.damp(field.rotation.y, targetY, 6, delta);
    field.rotation.x = THREE.MathUtils.damp(field.rotation.x, targetX, 6, delta);
    field.position.y = Math.sin(state.clock.elapsedTime * 0.12) * 0.035;
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
