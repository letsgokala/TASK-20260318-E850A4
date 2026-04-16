/**
 * The audit flagged that report generation used a GET request even though
 * it creates an ExportTask row and a file on disk. The fix is POST. This
 * test pins the verb so the regression can't silently return.
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
import ReportsPage from '../views/ReportsPage.vue'

function makeInstance(overrides = {}) {
  const ctx = {
    ...ReportsPage.data(),
    ...overrides,
  }
  for (const [name, fn] of Object.entries(ReportsPage.methods)) {
    ctx[name] = fn.bind(ctx)
  }
  // startPolling is called at the end of generateReport — stub it so we
  // don't spin up a real timer during the test.
  ctx.startPolling = () => {}
  return ctx
}

describe('ReportsPage.generateReport', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
  })

  it('uses POST (not GET) so the server can create the export task and file', async () => {
    api.post.mockResolvedValue({ data: { id: 'task-1', status: 'complete', report_type: 'reconciliation' } })

    const vm = makeInstance({
      form: { report_type: 'reconciliation', batch_id: '', from_date: '', to_date: '' },
    })

    await vm.generateReport()

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.get).not.toHaveBeenCalled()
    const url = api.post.mock.calls[0][0]
    expect(url).toMatch(/^\/reports\/generate\/reconciliation/)
  })

  it('includes batch_id and date filters in the query string', async () => {
    api.post.mockResolvedValue({ data: { id: 'task-2', status: 'pending', report_type: 'audit' } })

    const vm = makeInstance({
      form: {
        report_type: 'audit',
        batch_id: 'batch-123',
        from_date: '2026-01-01T00:00',
        to_date: '2026-06-01T00:00',
      },
    })

    await vm.generateReport()

    const url = api.post.mock.calls[0][0]
    expect(url).toContain('/reports/generate/audit')
    expect(url).toContain('batch_id=batch-123')
    expect(url).toContain('from_date=')
    expect(url).toContain('to_date=')
  })
})
