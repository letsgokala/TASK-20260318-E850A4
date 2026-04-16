/**
 * Two coverage areas on the review page:
 *
 * 1. The checklist-label fix (previously the page rendered raw UUIDs).
 * 2. The reviewer material-download control — the second audit report
 *    flagged the absence of this control as a High-severity gap because
 *    reviewers could not actually inspect uploaded evidence. The test
 *    here pins the handler's wiring against the backend download route
 *    and the blob-download flow used to avoid leaking the JWT into any
 *    ``<a href>`` target.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import RegistrationReviewPage from '../views/RegistrationReviewPage.vue'
import api from '../api.js'

describe('RegistrationReviewPage.checklistLabel', () => {
  it('returns the label when the item exists', () => {
    const ctx = {
      checklistItems: [
        { id: 'item-1', label: 'Research Proposal' },
        { id: 'item-2', label: 'Budget Plan' },
      ],
    }
    const label = RegistrationReviewPage.methods.checklistLabel.call(ctx, 'item-2')
    expect(label).toBe('Budget Plan')
  })

  it('falls back to the raw id when the item is missing', () => {
    const ctx = { checklistItems: [] }
    const label = RegistrationReviewPage.methods.checklistLabel.call(ctx, 'unknown-id')
    expect(label).toBe('unknown-id')
  })
})

describe('RegistrationReviewPage.downloadVersion', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('hits the backend material download route with a blob response', async () => {
    const fakeBlob = new Blob(['fake bytes'], { type: 'application/pdf' })
    const getSpy = vi.spyOn(api, 'get').mockResolvedValueOnce({
      data: fakeBlob,
      headers: { 'content-type': 'application/pdf' },
    })

    // window.URL.createObjectURL/revokeObjectURL are not implemented in
    // jsdom by default; stub them out so the handler can run.
    const createObjectURL = vi.fn(() => 'blob:mock-url')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: createObjectURL,
      configurable: true,
    })
    Object.defineProperty(window.URL, 'revokeObjectURL', {
      value: revokeObjectURL,
      configurable: true,
    })
    // document.createElement('a').click() triggers a navigation in jsdom —
    // intercept it so the test stays deterministic.
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})

    const ctx = {
      downloadError: {},
      downloadingVersion: {},
    }
    const ver = {
      id: 'ver-123',
      material_id: 'mat-1',
      original_filename: 'evidence.pdf',
    }

    await RegistrationReviewPage.methods.downloadVersion.call(ctx, ver)

    expect(getSpy).toHaveBeenCalledWith(
      '/registrations/versions/ver-123/download',
      { responseType: 'blob' },
    )
    expect(createObjectURL).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
    expect(ctx.downloadError['mat-1']).toBe('')
  })

  it('surfaces server error detail from a blob body', async () => {
    const errBlob = new Blob([JSON.stringify({ detail: 'Not your registration' })], {
      type: 'application/json',
    })
    vi.spyOn(api, 'get').mockRejectedValueOnce({
      response: { status: 403, data: errBlob },
    })

    const ctx = {
      downloadError: {},
      downloadingVersion: {},
    }
    const ver = {
      id: 'ver-123',
      material_id: 'mat-9',
      original_filename: 'nope.pdf',
    }

    await RegistrationReviewPage.methods.downloadVersion.call(ctx, ver)

    expect(ctx.downloadError['mat-9']).toBe('Not your registration')
    expect(ctx.downloadingVersion['ver-123']).toBe(false)
  })
})
