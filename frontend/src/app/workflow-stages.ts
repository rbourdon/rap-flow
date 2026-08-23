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
