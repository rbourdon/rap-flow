import { describe, expect, it } from 'vitest'
import { STAGE_ACTIVE_LABELS, STAGE_LABELS, WORKFLOW_STAGES, normalizeStage } from './workflow-stages'

// That WORKFLOW_STAGES matches backend/workflow.py STAGES is enforced from the
// backend side, in backend/tests/test_contracts.py.

describe('workflow stages', () => {
  it('labels every stage', () => {
    for (const stage of WORKFLOW_STAGES) {
      expect(STAGE_LABELS[stage]).toBeTruthy()
      expect(STAGE_ACTIVE_LABELS[stage]).toBeTruthy()
    }
  })

  it('normalizes machine ids to themselves', () => {
    for (const stage of WORKFLOW_STAGES) {
      expect(normalizeStage(stage)).toBe(stage)
    }
  })

  it('maps legacy human-readable labels written by older workers', () => {
    expect(normalizeStage('Downloading Audio')).toBe('ingest')
    expect(normalizeStage('Imagining drums')).toBe('groove')
    expect(normalizeStage('Synthesizing Beats')).toBe('render')
  })

  it('returns null for empty or unknown stages', () => {
    expect(normalizeStage(null)).toBeNull()
    expect(normalizeStage('')).toBeNull()
    expect(normalizeStage('COMPLETED')).toBeNull()
  })
})
