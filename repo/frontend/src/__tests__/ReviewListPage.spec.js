/**
 * The audit flagged that the batch-review comment was hardcoded to
 * "Batch <action> from review queue" regardless of what the reviewer typed.
 * These tests guard the fix: the user-entered comment must be sent to the
 * backend; absent a comment we fall back to a short default that still
 * contains the action.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('../api.js', () => {
  const api = {
    get: vi.fn(),
    post: vi.fn(),
  }
  return { default: api }
})

import api from '../api.js'
import ReviewListPage from '../views/ReviewListPage.vue'

function makeInstance(overrides = {}) {
  const ctx = {
    ...ReviewListPage.data(),
    ...overrides,
  }
  for (const [name, fn] of Object.entries(ReviewListPage.methods)) {
    ctx[name] = fn.bind(ctx)
  }
  return ctx
}

describe('ReviewListPage.batchAction', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
  })

  it('forwards the reviewer-entered comment verbatim', async () => {
    api.post.mockResolvedValue({ data: { succeeded: 2, failed: 0 } })
    api.get.mockResolvedValue({ data: { items: [], total: 0 } })

    const vm = makeInstance({
      selected: ['r-1', 'r-2'],
      batchComment: 'Looks good, approving.',
    })

    await vm.batchAction('approved')

    expect(api.post).toHaveBeenCalledWith('/reviews/batch', {
      registration_ids: ['r-1', 'r-2'],
      action: 'approved',
      comment: 'Looks good, approving.',
    })
  })

  it('falls back to a short default when the comment field is empty', async () => {
    api.post.mockResolvedValue({ data: { succeeded: 1, failed: 0 } })
    api.get.mockResolvedValue({ data: { items: [], total: 0 } })

    const vm = makeInstance({
      selected: ['r-1'],
      batchComment: '',
    })

    await vm.batchAction('rejected')

    expect(api.post).toHaveBeenCalledWith('/reviews/batch', {
      registration_ids: ['r-1'],
      action: 'rejected',
      comment: 'Batch rejected',
    })
  })

  it('no-ops when nothing is selected', async () => {
    const vm = makeInstance({ selected: [], batchComment: '' })

    await vm.batchAction('approved')

    expect(api.post).not.toHaveBeenCalled()
  })
})
