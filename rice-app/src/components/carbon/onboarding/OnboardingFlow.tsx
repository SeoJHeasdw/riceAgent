import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDemoAnimationSpeed } from '../../../lib/demo';
import { useI18n } from '../../../lib/i18n/I18nContext';
import type { ModelDraft, ModelSettings } from '../ModelPicker';
import { CinematicCanvas } from './cinematic/CinematicCanvas';
import {
  AwakeningOverlay,
  CinematicHeader,
  EcosystemOverlay,
  HandoffOverlay,
  LoopOverlay,
  ModelOverlay,
  PrivacyNote,
} from './cinematic/CinematicUI';
import { getCinematicCopy } from './sequences/cinematicCopy';
import { useCinematicSequence } from './sequences/useCinematicSequence';
import { useCinematicPreferences } from './useCinematicPreferences';

export interface OnboardingFlowProps {
  currentModel: string;
  modelMode: 'saas' | 'local';
  settings: ModelSettings;
  onComplete: (payload: { modelId: string; settings: ModelSettings; mode: 'saas' | 'local' }) => void;
}

/**
 * One continuous onboarding scene.
 *
 * The WebGL layer owns the persistent RICE core while the sequence state
 * changes only the camera composition and accessible DOM controls above it.
 * This keeps transitions spatially continuous and lets future scenes be added
 * without coupling model setup to the renderer.
 */
export const OnboardingFlow: React.FC<OnboardingFlowProps> = ({
  currentModel,
  modelMode,
  settings,
  onComplete,
}) => {
  const { language } = useI18n();
  const { t } = useTranslation('onboarding');
  const copy = useMemo(() => getCinematicCopy(language), [language]);
  const { reducedMotion, lowPower } = useCinematicPreferences();
  const [draft, setDraft] = useState<ModelDraft>({
    modelId: currentModel,
    settings,
    mode: modelMode,
    canSave: false,
  });

  const complete = useCallback(() => {
    onComplete({ modelId: draft.modelId, settings: draft.settings, mode: draft.mode });
  }, [draft, onComplete]);

  const sequence = useCinematicSequence(complete, reducedMotion);
  const stage = useRef<HTMLDivElement>(null);
  useDemoAnimationSpeed(stage);

  return (
    <div
      ref={stage}
      className={`cinematic-onboarding cinematic-onboarding--${sequence.phase}${reducedMotion ? ' is-reduced-motion' : ''}${lowPower ? ' is-low-power' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={t('ui.dialogAriaLabel')}
    >
      <div className="cinematic-nebula" aria-hidden="true" />
      <div className="cinematic-grid" aria-hidden="true" />
      <div key={sequence.phase} className="cinematic-phase-transition" aria-hidden="true" />
      <CinematicCanvas
        phase={sequence.phase}
        awakeningBeat={sequence.awakeningBeat}
        capabilityIndex={sequence.capabilityIndex}
        reducedMotion={reducedMotion}
        lowPower={lowPower}
      />

      <CinematicHeader phase={sequence.phase} copy={copy} onSkip={sequence.enterModel} />

      {sequence.phase === 'awakening' && (
        <AwakeningOverlay beat={sequence.awakeningBeat} copy={copy} />
      )}
      {sequence.phase === 'loop' && (
        <LoopOverlay copy={copy} onNext={sequence.enterEcosystem} />
      )}
      {sequence.phase === 'ecosystem' && (
        <EcosystemOverlay
          copy={copy}
          capabilityIndex={sequence.capabilityIndex}
          onSelect={sequence.selectCapability}
          onAdvance={sequence.advanceCapability}
          onContinue={sequence.enterModel}
          reducedMotion={reducedMotion}
        />
      )}
      {sequence.phase === 'model' && (
        <ModelOverlay
          copy={copy}
          draft={draft}
          currentModel={currentModel}
          onChange={setDraft}
          onBack={sequence.returnToEcosystem}
          onConnect={sequence.startHandoff}
        />
      )}
      {sequence.phase === 'handoff' && <HandoffOverlay copy={copy} />}

      {sequence.phase !== 'model' && sequence.phase !== 'handoff' && (
        <PrivacyNote>{copy.privacy}</PrivacyNote>
      )}
      <div className="cinematic-vignette" aria-hidden="true" />
    </div>
  );
};
