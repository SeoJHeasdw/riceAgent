import React from 'react';
import type { ModelDraft } from '../../ModelPicker';
import { ModelPicker } from '../../ModelPicker';
import { BrandMark } from '../primitives';
import type { CinematicCopy } from '../sequences/cinematicCopy';
import type { AwakeningBeat, CinematicPhase } from '../sequences/types';

const ArrowIcon = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

const ShieldIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12 3 4.5 6.2v5.5c0 4.7 3.1 8 7.5 9.3 4.4-1.3 7.5-4.6 7.5-9.3V6.2L12 3Z" />
    <path d="m9.3 12.2 1.8 1.8 3.8-4" />
  </svg>
);

const CapabilityIcon = ({ id }: { id: 'skill' | 'connector' | 'plugin' }) => {
  if (id === 'skill') {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M12 3 5 6.5v5.8c0 4.1 2.7 7 7 8.7 4.3-1.7 7-4.6 7-8.7V6.5L12 3Z" />
        <path d="m9.5 12 1.6 1.7 3.7-4" />
      </svg>
    );
  }
  if (id === 'connector') {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M9.5 14.5 14.5 9" /><path d="M7.3 17.2 5.8 18.7a3.6 3.6 0 0 1-5.1-5.1l3.4-3.4a3.6 3.6 0 0 1 5.1 0" /><path d="m16.7 6.8 1.5-1.5a3.6 3.6 0 1 1 5.1 5.1l-3.4 3.4a3.6 3.6 0 0 1-5.1 0" />
      </svg>
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M8.5 3v5.5H3M15.5 3v5.5H21M8.5 21v-5.5H3M15.5 21v-5.5H21" />
      <rect x="7" y="7" width="10" height="10" rx="2" />
    </svg>
  );
};

export const CinematicHeader = ({ phase, copy, onSkip }: { phase: CinematicPhase; copy: CinematicCopy; onSkip: () => void }) => {
  const stages: Array<{ id: CinematicPhase; label: string }> = [
    { id: 'loop', label: 'LEARN' },
    { id: 'ecosystem', label: 'EXPAND' },
    { id: 'model', label: 'CONNECT' },
  ];
  const activeIndex = phase === 'awakening' ? -1 : phase === 'handoff' ? 3 : stages.findIndex((stage) => stage.id === phase);

  return (
    <header className="cinematic-header">
      <div className="cinematic-brand" aria-label="RICE">
        <span className="cinematic-brand__mark"><BrandMark size={18} /></span>
        <span className="cinematic-brand__name">RICE</span>
        <span className="cinematic-brand__status">EVOLUTION SYSTEM</span>
      </div>
      <div className="cinematic-progress" aria-label="Onboarding progress">
        {stages.map((stage, index) => (
          <span key={stage.id} className={`cinematic-progress__item ${index <= activeIndex ? 'is-active' : ''}`}>
            <i />
            {stage.label}
          </span>
        ))}
      </div>
      {phase !== 'model' && phase !== 'handoff' && (
        <button type="button" className="cinematic-skip" onClick={onSkip}>{copy.skip}</button>
      )}
    </header>
  );
};

export const PrivacyNote = ({ children }: { children: React.ReactNode }) => (
  <div className="cinematic-privacy"><ShieldIcon />{children}</div>
);

export const AwakeningOverlay = ({ beat, copy }: { beat: AwakeningBeat; copy: CinematicCopy }) => (
  <section className={`awakening-overlay is-${beat}`} aria-live="polite">
    <div className="awakening-readout">
      <span className="cinematic-eyebrow">{copy.awakening.eyebrow}</span>
      <span className="awakening-readout__line" />
      <span>{beat === 'awake' ? copy.awakening.awake : copy.awakening.assembling}</span>
    </div>
    <div className="awakening-title">
      <h1>{copy.awakening.title}</h1>
      <p>{copy.awakening.description}</p>
    </div>
    <div className="awakening-loader" aria-hidden="true"><span /></div>
  </section>
);

export const LoopOverlay = ({ copy, onNext }: { copy: CinematicCopy; onNext: () => void }) => (
  <section className="loop-overlay cinematic-enter">
    <div className="cinematic-copy-block">
      <span className="cinematic-eyebrow">{copy.loop.eyebrow}</span>
      <h1>{copy.loop.title}</h1>
      <p>{copy.loop.description}</p>
    </div>
    <div className="loop-nodes" aria-label={copy.loop.eyebrow}>
      {copy.loop.nodes.map((node, index) => (
        <div key={node.label} className={`loop-node loop-node--${index + 1}`}>
          <span className="loop-node__number">0{index + 1}</span>
          <span><strong>{node.label}</strong><small>{node.caption}</small></span>
        </div>
      ))}
    </div>
    <div className="loop-drag-hint" aria-hidden="true">
      <span>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 11V7a2 2 0 1 1 4 0v3-5a2 2 0 1 1 4 0v6-3a2 2 0 1 1 4 0v6c0 4.4-3.6 8-8 8h-1.2a8 8 0 0 1-6.6-3.5L2.7 16a2 2 0 0 1 3.2-2.4L8 16" />
        </svg>
      </span>
      {copy.loop.dragHint}
    </div>
    <button type="button" className="cinematic-primary cinematic-primary--floating" onClick={onNext}>
      <span>{copy.loop.next}</span><ArrowIcon />
    </button>
  </section>
);

export const EcosystemOverlay = ({
  copy,
  capabilityIndex,
  onSelect,
  onAdvance,
  onContinue,
}: {
  copy: CinematicCopy;
  capabilityIndex: number;
  onSelect: (index: number) => void;
  onAdvance: () => void;
  onContinue: () => void;
}) => {
  const capability = copy.ecosystem.capabilities[capabilityIndex];
  const complete = capabilityIndex === copy.ecosystem.capabilities.length - 1;
  return (
    <section className="ecosystem-overlay cinematic-enter">
      <div className="ecosystem-panel">
        <span className="cinematic-eyebrow">{copy.ecosystem.eyebrow}</span>
        <h1>{copy.ecosystem.title}</h1>
        <p className="ecosystem-panel__intro">{copy.ecosystem.description}</p>
        <div key={capability.id} className="capability-card cinematic-enter" style={{ '--capability-accent': capability.accent } as React.CSSProperties}>
          <div className="capability-card__topline">
            <span>{capability.eyebrow}</span>
            <i><CapabilityIcon id={capability.id} /></i>
          </div>
          <h2>{capability.title}</h2>
          <p>{capability.description}</p>
          <div className="capability-card__metric"><span />{capability.metric}</div>
        </div>
        <div className="capability-tabs">
          {copy.ecosystem.capabilities.map((item, index) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelect(index)}
              className={index === capabilityIndex ? 'is-active' : index < capabilityIndex ? 'is-complete' : ''}
              aria-label={item.title}
            >
              <span><CapabilityIcon id={item.id} /></span>
              <small>{item.title}</small>
            </button>
          ))}
        </div>
        {complete && <p className="ecosystem-panel__assembled">{copy.ecosystem.assembled}</p>}
        <button type="button" className="cinematic-primary" onClick={complete ? onContinue : onAdvance}>
          <span>{complete ? copy.ecosystem.continue : copy.ecosystem.add}</span><ArrowIcon />
        </button>
      </div>
    </section>
  );
};

export const ModelOverlay = ({
  copy,
  draft,
  currentModel,
  onChange,
  onBack,
  onConnect,
}: {
  copy: CinematicCopy;
  draft: ModelDraft;
  currentModel: string;
  onChange: (draft: ModelDraft) => void;
  onBack: () => void;
  onConnect: () => void;
}) => (
  <section className="model-overlay cinematic-enter">
    <div className="model-dock rice-model-theme">
      <div className="model-dock__header">
        <div>
          <span className="cinematic-eyebrow">{copy.model.eyebrow}</span>
          <h1>{copy.model.title}</h1>
          <p>{copy.model.description}</p>
        </div>
        <span className="model-dock__signal"><i />CORE LINK</span>
      </div>
      <div className="model-dock__body">
        <ModelPicker
          currentModel={draft.modelId || currentModel}
          modelMode={draft.mode}
          settings={draft.settings}
          onboarding
          onChange={onChange}
        />
      </div>
      <div className="model-dock__footer">
        <button type="button" className="cinematic-back" onClick={onBack}>{copy.model.back}</button>
        <div className="model-dock__privacy"><ShieldIcon />{copy.model.local}</div>
        <div className="model-dock__action">
          {!draft.canSave && <span>{copy.model.blocked}</span>}
          <button type="button" className="cinematic-primary" disabled={!draft.canSave} onClick={onConnect}>
            <span>{copy.model.connect}</span><ArrowIcon />
          </button>
        </div>
      </div>
    </div>
  </section>
);

export const HandoffOverlay = ({ copy }: { copy: CinematicCopy }) => (
  <section className="handoff-overlay" aria-live="polite">
    <span className="handoff-overlay__check">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="m5 12 4 4L19 6" />
      </svg>
    </span>
    <span className="cinematic-eyebrow">{copy.handoff.eyebrow}</span>
    <h1>{copy.handoff.title}</h1>
    <p>{copy.handoff.description}</p>
  </section>
);
