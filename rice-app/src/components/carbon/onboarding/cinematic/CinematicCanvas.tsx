import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Canvas } from '@react-three/fiber';
import * as THREE from 'three';
import type { AwakeningBeat, CinematicPhase, SceneDragState } from '../sequences/types';
import { CoreScene } from './CoreScene';

interface CinematicCanvasProps {
  phase: CinematicPhase;
  awakeningBeat: AwakeningBeat;
  capabilityIndex: number;
  reducedMotion: boolean;
  lowPower: boolean;
}

const isInteractivePhase = (phase: CinematicPhase) =>
  phase === 'loop' || phase === 'ecosystem' || phase === 'model';

export const CinematicCanvas = ({
  phase,
  awakeningBeat,
  capabilityIndex,
  reducedMotion,
  lowPower,
}: CinematicCanvasProps) => {
  const drag = useRef<SceneDragState>({
    dragging: false,
    lastX: 0,
    lastY: 0,
    rotationX: 0,
    rotationY: 0,
    velocityX: 0,
    velocityY: 0,
  });
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (isInteractivePhase(phase) && !reducedMotion) return;
    drag.current.dragging = false;
    drag.current.velocityX = 0;
    drag.current.velocityY = 0;
    if (reducedMotion) {
      drag.current.rotationX = 0;
      drag.current.rotationY = 0;
    }
    setDragging(false);
  }, [phase, reducedMotion]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!isInteractivePhase(phase) || reducedMotion || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current.dragging = true;
    drag.current.lastX = event.clientX;
    drag.current.lastY = event.clientY;
    setDragging(true);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current.dragging || !isInteractivePhase(phase) || reducedMotion) return;
    const dx = event.clientX - drag.current.lastX;
    const dy = event.clientY - drag.current.lastY;
    drag.current.lastX = event.clientX;
    drag.current.lastY = event.clientY;
    drag.current.rotationY += dx * 0.0027;
    drag.current.rotationX = THREE.MathUtils.clamp(drag.current.rotationX + dy * 0.0021, -0.58, 0.58);
    drag.current.velocityY = dx * 0.00042;
    drag.current.velocityX = dy * 0.00032;
  };

  const stopDragging = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current.dragging) return;
    drag.current.dragging = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  };

  const interactive = isInteractivePhase(phase) && !reducedMotion;

  return (
    <div
      className={`cinematic-viewport${interactive ? ' is-draggable' : ''}${dragging ? ' is-dragging' : ''}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={stopDragging}
      onPointerCancel={stopDragging}
      onPointerLeave={(event) => {
        if (event.buttons === 0) stopDragging(event);
      }}
      aria-hidden="true"
    >
      <Canvas
        className="!absolute inset-0"
        camera={{ position: [0, 0, 7.5], fov: 42, near: 0.1, far: 80 }}
        dpr={lowPower ? 1 : [1, 1.55]}
        gl={{ antialias: !lowPower, alpha: true, powerPreference: 'high-performance' }}
        onCreated={({ gl }) => {
          gl.toneMapping = THREE.ACESFilmicToneMapping;
          gl.toneMappingExposure = 1.12;
          gl.setClearColor('#101319', 0);
        }}
        fallback={<div className="cinematic-core-fallback" />}
      >
        <CoreScene
          phase={phase}
          awakeningBeat={awakeningBeat}
          capabilityIndex={capabilityIndex}
          drag={drag}
          reducedMotion={reducedMotion}
          lowPower={lowPower}
        />
      </Canvas>
    </div>
  );
};
