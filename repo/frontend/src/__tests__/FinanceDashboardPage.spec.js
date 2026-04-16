/**
 * Smoke tests for the finance dashboard.
 *
 * The audit flagged two concrete regressions we want to guard against:
 *   1. Category filter / date range is not forwarded to `/finance/statistics`.
 *   2. The "Record Transaction" button posts with an empty category.
 *
 * These tests instantiate only the component's data+methods (not the full
 * template) so they run quickly under jsdom without a live backend.
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
import FinanceDashboardPage from '../views/FinanceDashboardPage.vue'

/**
 * Build an isolated instance of the component's data/methods, bypassing
 * the reactive template. Enough to test the data-flow behaviors we care
 * about without mounting the full DOM.
 */
function makeInstance(overrides = {}) {
  const ctx = {
    ...FinanceDashboardPage.data(),
    ...overrides,
    selectedAccount: overrides.selectedAccount || null,
    txnForm: overrides.txnForm || {
      type: 'expense',
      amount: null,
      category: '',
      description: '',
    },
  }
  // Bind every method to ctx so `this.*` resolves correctly.
  for (const [name, fn] of Object.entries(FinanceDashboardPage.methods)) {
    ctx[name] = fn.bind(ctx)
  }
  return ctx
}

describe('FinanceDashboardPage.loadStats', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
  })

  it('sends no params when no filters are set', async () => {
    api.get.mockResolvedValue({ data: { items: [], grand_total_income: 0, grand_total_expense: 0 } })
    const vm = makeInstance()

    await vm.loadStats()

    expect(api.get).toHaveBeenCalledWith('/finance/statistics', { params: {} })
  })

  it('forwards category/from/to filters to the backend', async () => {
    api.get.mockResolvedValue({ data: { items: [], grand_total_income: 0, grand_total_expense: 0 } })
    const vm = makeInstance({
      statsCategory: 'travel',
      statsFrom: '2026-01-01',
      statsTo: '2026-12-31',
    })

    await vm.loadStats()

    expect(api.get).toHaveBeenCalledWith('/finance/statistics', {
      params: {
        category: 'travel',
        from_date: '2026-01-01',
        to_date: '2026-12-31',
      },
    })
  })
})

describe('FinanceDashboardPage.createTransaction', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
  })

  it('blocks submission when category is empty', async () => {
    const vm = makeInstance({
      selectedAccount: { id: 'acct-1' },
      txnForm: { type: 'expense', amount: 50, category: '', description: 'test' },
    })

    await vm.createTransaction()

    expect(vm.txnError).toMatch(/category is required/i)
    expect(api.post).not.toHaveBeenCalled()
  })

  it('blocks submission when amount is missing', async () => {
    const vm = makeInstance({
      selectedAccount: { id: 'acct-1' },
      txnForm: { type: 'expense', amount: null, category: 'travel', description: '' },
    })

    await vm.createTransaction()

    expect(vm.txnError).toMatch(/amount/i)
    expect(api.post).not.toHaveBeenCalled()
  })

  it('posts a valid transaction when category and amount are present', async () => {
    api.post.mockResolvedValue({ data: { id: 'txn-1' } })
    api.get.mockResolvedValue({ data: { balance: 0, total_income: 0, total_expenses: 0, allocated_budget: 0 } })

    const vm = makeInstance({
      selectedAccount: { id: 'acct-1' },
      txnForm: { type: 'expense', amount: 75.5, category: 'travel', description: 'Cab' },
    })

    await vm.createTransaction()

    expect(api.post).toHaveBeenCalledWith(
      '/finance/accounts/acct-1/transactions',
      expect.objectContaining({
        type: 'expense',
        amount: 75.5,
        category: 'travel',
        description: 'Cab',
        over_budget_confirmed: false,
      }),
    )
    expect(vm.txnError).toBe('')
  })
})
