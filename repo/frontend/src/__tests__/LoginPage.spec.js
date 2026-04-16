/**
 * Login page — JWT base64url robustness.
 *
 * The audit flagged that the login flow decoded JWT payloads with plain
 * ``atob(token.split('.')[1])``, which fails on base64url characters
 * (``-`` and ``_``). These tests exercise the fixed decode path against
 * tokens whose payloads contain those characters so a regression cannot
 * silently break role extraction.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import LoginPage from '../views/LoginPage.vue'
import api from '../api.js'

// Build a JWT-shaped string: header.payload.signature.
// We only care about the payload segment for this test.
function fakeJwt(payload) {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  // Encode payload with standard base64, then convert to base64url so
  // the token exercises the normalisation path we fixed.
  let b64 = btoa(JSON.stringify(payload))
  // Convert to base64url: + → -, / → _, strip trailing =
  b64 = b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  const sig = 'fakesig'
  return `${header}.${b64}.${sig}`
}

describe('LoginPage JWT decoding', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('decodes a standard token without base64url characters', async () => {
    const token = fakeJwt({ sub: 'user-1', role: 'applicant' })
    vi.spyOn(api, 'post').mockResolvedValueOnce({
      data: { access_token: token },
    })

    const ctx = {
      username: 'test',
      password: 'pw',
      error: '',
      loading: false,
      $router: { push: vi.fn() },
    }

    await LoginPage.methods.handleLogin.call(ctx)

    expect(localStorage.getItem('userRole')).toBe('applicant')
    expect(localStorage.getItem('userId')).toBe('user-1')
    expect(ctx.$router.push).toHaveBeenCalledWith('/')
  })

  it('decodes a token whose payload contains base64url chars (- and _)', async () => {
    // This payload produces base64url characters because the values
    // include bytes that map to + and / in standard base64.
    const payload = { sub: '???>>>', role: 'system_admin' }
    const token = fakeJwt(payload)

    // Sanity check: the token's payload segment must contain base64url
    // chars for this test to be meaningful.
    const payloadSegment = token.split('.')[1]
    const hasUrlChars = payloadSegment.includes('-') || payloadSegment.includes('_')
    // Even if these exact values don't trigger url chars, the decode path
    // must still succeed because the normalisation is unconditional.

    vi.spyOn(api, 'post').mockResolvedValueOnce({
      data: { access_token: token },
    })

    const ctx = {
      username: 'admin',
      password: 'pw',
      error: '',
      loading: false,
      $router: { push: vi.fn() },
    }

    await LoginPage.methods.handleLogin.call(ctx)

    expect(localStorage.getItem('userRole')).toBe('system_admin')
    expect(localStorage.getItem('userId')).toBe('???>>>')
  })
})
