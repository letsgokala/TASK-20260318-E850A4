/**
 * Registration wizard — supplementary aggregate-size validation.
 *
 * The audit flagged that the supplementary upload path validated only
 * per-file size, not the 200 MB aggregate total. The Prompt requires
 * real-time validation for both single-file AND total upload limits,
 * so the missing aggregate check was a Medium defect.
 *
 * These tests drive the component's methods directly (no template
 * mount) so they run quickly under jsdom without a backend and pin
 * both the per-file and per-total enforcement paths.
 */
import { describe, it, expect } from 'vitest'
import RegistrationWizardPage from '../views/RegistrationWizardPage.vue'

function makeCtx(overrides = {}) {
  return {
    // Mirror the data() shape the handler touches.
    suppFileErrors: {},
    suppFiles: {},
    uploadInfo: {
      used_bytes: 0,
      limit_bytes: 200 * 1024 * 1024, // 200 MB
      remaining_bytes: 200 * 1024 * 1024,
      supplementary_eligible: true,
      supplementary_used: false,
    },
    ...overrides,
    $set: null, // trigger Vue 3 branch
  }
}

function fakeFile(name, sizeBytes, type = 'application/pdf') {
  // jsdom's File respects size via the blob constructor length.
  const payload = new Uint8Array(sizeBytes)
  return new File([payload], name, { type })
}

function fakeEvent(file) {
  return { target: { files: [file], value: '' } }
}

describe('RegistrationWizardPage.handleSuppFileSelect', () => {
  it('accepts a supplementary file under both caps', () => {
    const ctx = makeCtx()
    const file = fakeFile('doc.pdf', 5 * 1024 * 1024) // 5 MB
    const evt = fakeEvent(file)

    RegistrationWizardPage.methods.handleSuppFileSelect.call(
      ctx,
      { id: 'ci-1' },
      evt,
    )

    expect(ctx.suppFiles['ci-1']).toBe(file)
    expect(ctx.suppFileErrors['ci-1']).toBe('')
  })

  it('rejects a supplementary file that itself exceeds the 20 MB per-file cap', () => {
    const ctx = makeCtx()
    const file = fakeFile('huge.pdf', 25 * 1024 * 1024) // 25 MB
    const evt = fakeEvent(file)

    RegistrationWizardPage.methods.handleSuppFileSelect.call(
      ctx,
      { id: 'ci-1' },
      evt,
    )

    expect(ctx.suppFiles['ci-1']).toBeUndefined()
    expect(ctx.suppFileErrors['ci-1']).toMatch(/exceeds 20 MB/)
  })

  it('rejects a supplementary file that would push the aggregate past 200 MB', () => {
    // 190 MB already used, 15 MB already staged for another item, 10 MB
    // incoming — total projection is 215 MB, which is over the cap.
    const existing = fakeFile('prior.pdf', 15 * 1024 * 1024)
    const ctx = makeCtx({
      uploadInfo: {
        used_bytes: 190 * 1024 * 1024,
        limit_bytes: 200 * 1024 * 1024,
        remaining_bytes: 10 * 1024 * 1024,
        supplementary_eligible: true,
        supplementary_used: false,
      },
      suppFiles: { 'ci-other': existing },
    })

    const incoming = fakeFile('incoming.pdf', 10 * 1024 * 1024)
    RegistrationWizardPage.methods.handleSuppFileSelect.call(
      ctx,
      { id: 'ci-new' },
      fakeEvent(incoming),
    )

    expect(ctx.suppFiles['ci-new']).toBeUndefined()
    expect(ctx.suppFileErrors['ci-new']).toMatch(/200 MB storage limit/)
  })

  it('recomputes the projection when replacing the file for the same checklist item', () => {
    // Already staged 5 MB for ci-1; picking a replacement 15 MB file for
    // the same slot should NOT count both — only the incoming file
    // against the slot's bucket.
    const prior = fakeFile('prior.pdf', 5 * 1024 * 1024)
    const ctx = makeCtx({
      uploadInfo: {
        used_bytes: 190 * 1024 * 1024,
        limit_bytes: 200 * 1024 * 1024,
        remaining_bytes: 10 * 1024 * 1024,
        supplementary_eligible: true,
        supplementary_used: false,
      },
      suppFiles: { 'ci-1': prior },
    })

    const replacement = fakeFile('better.pdf', 8 * 1024 * 1024) // 190 + 8 = 198 <= 200
    RegistrationWizardPage.methods.handleSuppFileSelect.call(
      ctx,
      { id: 'ci-1' },
      fakeEvent(replacement),
    )

    expect(ctx.suppFiles['ci-1']).toBe(replacement)
    expect(ctx.suppFileErrors['ci-1']).toBe('')
  })
})
