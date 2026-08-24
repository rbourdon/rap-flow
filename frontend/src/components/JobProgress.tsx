'use client'

import React, { useEffect, useState } from 'react';
import {
  WORKFLOW_STAGES,
  STAGE_LABELS,
  normalizeStage,
  type WorkflowStage,
} from '@/app/workflow-stages';

interface StageState {
  state?: string;
  reused?: boolean;
  updatedAt?: string;
}

interface JobProgressProps {
  status: string;
  // Machine-readable stage id (e.g. "separate"). Legacy display-string values
  // are tolerated via normalizeStage.
  stage: string | null;
  createdAt?: Date | string | null;
  stageStates?: Record<string, StageState> | null;
}

function formatSeconds(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0) return '';
  const s = Math.round(totalSeconds);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m > 0) return `${m}m ${rem}s`;
  return `${rem}s`;
}

// Per-stage duration approximated from the durable stageStates timestamps:
// a stage's duration is the gap between its completion time and the previous
// stage's completion time (or the job's creation for the first stage).
function computeStageDurations(
  stageStates: Record<string, StageState> | null | undefined,
  createdAt: Date | string | null | undefined,
): Partial<Record<WorkflowStage, number>> {
  const out: Partial<Record<WorkflowStage, number>> = {};
  if (!stageStates) return out;
  let prev = createdAt ? new Date(createdAt).getTime() : NaN;
  for (const stage of WORKFLOW_STAGES) {
    const entry = stageStates[stage];
    if (!entry?.updatedAt) continue;
    const t = new Date(entry.updatedAt).getTime();
    if (Number.isFinite(prev) && Number.isFinite(t) && t >= prev) {
      out[stage] = (t - prev) / 1000;
    }
    prev = t;
  }
  return out;
}

export function JobProgress({ status, stage, createdAt, stageStates }: JobProgressProps) {
  const normalized = normalizeStage(stage);
  const currentIndex =
    status === 'COMPLETED'
      ? WORKFLOW_STAGES.length
      : status === 'PENDING'
      ? -1
      : normalized
      ? WORKFLOW_STAGES.indexOf(normalized)
      : 0;

  const durations = computeStageDurations(stageStates, createdAt);

  // Live elapsed timer while the job is in flight.
  const isActive = status === 'PENDING' || status === 'PROCESSING';
  const [elapsed, setElapsed] = useState<number | null>(null);
  useEffect(() => {
    if (!isActive || !createdAt) return;
    const start = new Date(createdAt).getTime();
    const tick = () => setElapsed((Date.now() - start) / 1000);
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [isActive, createdAt]);

  return (
    <div className="w-full max-w-md mx-auto py-8">
      {isActive && elapsed != null && (
        <p className="text-sm text-neutral-400 mb-6 text-center">
          Elapsed: {formatSeconds(elapsed)}
        </p>
      )}
      <div className="relative">
        {/* The continuous vertical line behind nodes */}
        <div className="absolute left-4 top-4 bottom-4 w-0.5 bg-neutral-800" />

        <div className="flex flex-col space-y-6">
          {WORKFLOW_STAGES.map((s, idx) => {
            let state: 'waiting' | 'active' | 'completed' | 'failed' = 'waiting';

            if (status === 'FAILED' && idx === currentIndex) {
              state = 'failed';
            } else if (idx < currentIndex || status === 'COMPLETED') {
              state = 'completed';
            } else if (idx === currentIndex && status !== 'FAILED') {
              state = 'active';
            }

            const label = STAGE_LABELS[s];
            const entry = stageStates?.[s];
            const dur = durations[s];

            return (
              <div key={s} className="relative flex items-center gap-6">
                <div className="relative z-10 flex items-center justify-center w-8 h-8 rounded-full shadow shrink-0">
                  {state === 'completed' && (
                    <div className="w-8 h-8 bg-green-500 rounded-full flex items-center justify-center text-white ring-4 ring-black">
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                  )}
                  {state === 'active' && (
                    <div className="w-8 h-8 bg-indigo-500 rounded-full flex items-center justify-center ring-4 ring-black">
                      <span className="w-3 h-3 bg-white rounded-full motion-safe:animate-pulse" />
                    </div>
                  )}
                  {state === 'waiting' && (
                    <div className="w-8 h-8 bg-neutral-800 rounded-full ring-4 ring-black" />
                  )}
                  {state === 'failed' && (
                    <div className="w-8 h-8 bg-red-500 rounded-full flex items-center justify-center text-white ring-4 ring-black">
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </div>
                  )}
                </div>
                <div className="flex-1 flex items-center justify-between gap-3">
                  <div className={`text-base font-medium transition-colors duration-300 ${
                    state === 'active' ? 'text-indigo-400' :
                    state === 'completed' ? 'text-neutral-300' :
                    state === 'failed' ? 'text-red-400' :
                    'text-neutral-500'
                  }`}>
                    {label}
                    {state === 'active' && <span className="ml-2 inline-block motion-safe:animate-bounce">…</span>}
                    {state === 'completed' && entry?.reused && (
                      <span className="ml-2 text-xs text-neutral-500">(reused)</span>
                    )}
                  </div>
                  {state === 'completed' && dur != null && dur >= 0 && (
                    <span className="text-xs text-neutral-500 tabular-nums">{formatSeconds(dur)}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
