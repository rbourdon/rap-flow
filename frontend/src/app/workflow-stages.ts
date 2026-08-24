// Machine-readable workflow stages, in execution order. Must stay in sync with
// backend/workflow.py STAGES. Kept in a plain (non-"use server") module so the
// value can be exported and imported by both server actions and client
// components - a "use server" file may only export async functions.
export const WORKFLOW_STAGES = [
  'ingest',
  'separate',
  'detect',
  'render',
  'finalize',
] as const

export type WorkflowStage = (typeof WORKFLOW_STAGES)[number]

// Human-readable labels for each stage. Single source of truth for the UI:
// the worker persists the machine id (stageKey) to `job.stage`, and the UI
// derives the label from this map. Must stay in sync with the backend's
// workflow.STAGE_LABELS.
export const STAGE_LABELS: Record<WorkflowStage, string> = {
  ingest: 'Downloading audio',
  separate: 'Separating stems',
  detect: 'Detecting onsets',
  render: 'Rendering percussion',
  finalize: 'Saving results',
}

// Short present-continuous labels used on compact list chips ("Separating…").
export const STAGE_ACTIVE_LABELS: Record<WorkflowStage, string> = {
  ingest: 'Downloading…',
  separate: 'Separating stems…',
  detect: 'Detecting onsets…',
  render: 'Rendering percussion…',
  finalize: 'Saving results…',
}

// The worker historically wrote human-readable labels (e.g. "Downloading
// Audio") to `job.stage` instead of the machine id. Accept either form so old
// in-flight jobs still map to a stage. Returns the machine id or null.
const LEGACY_STAGE_LABELS: Record<string, WorkflowStage> = {
  'Downloading Audio': 'ingest',
  'Separating Vocals': 'separate',
  'Analyzing Syllables': 'detect',
  'Synthesizing Beats': 'render',
  'Saving Results': 'finalize',
}

export function normalizeStage(stage: string | null | undefined): WorkflowStage | null {
  if (!stage) return null
  if ((WORKFLOW_STAGES as readonly string[]).includes(stage)) {
    return stage as WorkflowStage
  }
  return LEGACY_STAGE_LABELS[stage] ?? null
}
