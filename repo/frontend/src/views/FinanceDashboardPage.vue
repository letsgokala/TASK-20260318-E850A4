<template>
  <div>
    <div class="page-header">
      <h1>Finance Dashboard</h1>
      <p>Manage funding accounts and transactions.</p>
    </div>

    <!-- Statistics Filters -->
    <div class="filters mb-1">
      <input v-model="statsCategory" type="text" class="form-control" placeholder="Filter by category..." style="width: auto; min-width: 160px;" @change="loadStats" />
      <input v-model="statsFrom" type="date" class="form-control" style="width: auto;" @change="loadStats" />
      <input v-model="statsTo" type="date" class="form-control" style="width: auto;" @change="loadStats" />
    </div>

    <!-- Surface any data-load errors instead of silently showing empty panels. -->
    <div v-if="statsError" class="alert alert-error" data-test="finance-stats-error">{{ statsError }}</div>
    <div v-if="accountsError" class="alert alert-error" data-test="finance-accounts-error">{{ accountsError }}</div>
    <div v-if="summaryError" class="alert alert-error" data-test="finance-summary-error">{{ summaryError }}</div>
    <div v-if="txnsError" class="alert alert-error" data-test="finance-txns-error">{{ txnsError }}</div>
    <div v-if="registrationsError" class="alert alert-warning" data-test="finance-registrations-error">{{ registrationsError }}</div>

    <!-- Statistics -->
    <div v-if="stats" class="stats-row">
      <div class="stat-card">
        <div class="stat-value">{{ formatCurrency(stats.grand_total_income) }}</div>
        <div class="stat-label">Total Income</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ formatCurrency(stats.grand_total_expense) }}</div>
        <div class="stat-label">Total Expense</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ formatCurrency(stats.grand_total_income - stats.grand_total_expense) }}</div>
        <div class="stat-label">Net Balance</div>
      </div>
    </div>

    <!-- Category Breakdown -->
    <div v-if="stats && stats.items && stats.items.length > 0" class="card mb-2">
      <div class="card-header">By Category</div>
      <div class="card-body">
        <table>
          <thead>
            <tr>
              <th>Category</th>
              <th>Income</th>
              <th>Expense</th>
              <th>Net</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in stats.items" :key="item.category">
              <td>{{ item.category || '(uncategorized)' }}</td>
              <td class="text-success">{{ formatCurrency(item.total_income) }}</td>
              <td class="text-danger">{{ formatCurrency(item.total_expense) }}</td>
              <td>{{ formatCurrency(item.total_income - item.total_expense) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Funding Accounts List -->
    <div class="card mb-2">
      <div class="card-header flex justify-between items-center">
        <span>Funding Accounts</span>
        <div class="flex gap-1 items-center">
          <select v-if="registrations.length > 0" v-model="focusedRegistrationId" class="form-control" style="width: auto; font-size: 12px;">
            <option value="">All Registrations</option>
            <option v-for="reg in registrations" :key="reg.id" :value="reg.id">
              {{ reg.title || '(Untitled)' }} &ndash; {{ reg.status }}
            </option>
          </select>
          <button class="btn btn-sm btn-primary" @click="showCreateAccount = true">+ New Account</button>
        </div>
      </div>
      <div class="card-body">
        <div v-if="loadingAccounts" class="text-center"><span class="spinner"></span></div>
        <table v-else-if="accounts.length > 0">
          <thead>
            <tr>
              <th>Name</th>
              <th>Registration</th>
              <th>Allocated Budget</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="acct in filteredAccounts" :key="acct.id" :class="{ 'highlighted-row': acct.registration_id === focusedRegistrationId }">
              <td>{{ acct.name }}</td>
              <td class="text-muted" style="font-size: 11px;">{{ registrationLabel(acct.registration_id) }}</td>
              <td>{{ formatCurrency(acct.allocated_budget) }}</td>
              <td>
                <button class="btn btn-sm btn-outline" @click="selectAccount(acct)">
                  Manage
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-muted">No funding accounts yet.</p>
      </div>
    </div>

    <!-- Selected Account Detail -->
    <div v-if="selectedAccount" class="card mb-2">
      <div class="card-header flex justify-between items-center">
        <span>Account: {{ selectedAccount.name }}</span>
        <button class="btn btn-sm btn-outline" @click="selectedAccount = null; accountSummary = null; transactions = [];">
          Close
        </button>
      </div>
      <div class="card-body">
        <!-- Account Summary -->
        <div v-if="accountSummary" class="stats-row mb-2">
          <div class="stat-card">
            <div class="stat-value">{{ formatCurrency(accountSummary.allocated_budget) }}</div>
            <div class="stat-label">Budget</div>
          </div>
          <div class="stat-card">
            <div class="stat-value text-success">{{ formatCurrency(accountSummary.total_income) }}</div>
            <div class="stat-label">Income</div>
          </div>
          <div class="stat-card">
            <div class="stat-value text-danger">{{ formatCurrency(accountSummary.total_expenses) }}</div>
            <div class="stat-label">Expenses</div>
          </div>
          <div class="stat-card">
            <div class="stat-value" :class="{ 'text-danger': accountSummary.overspending }">
              {{ formatCurrency(accountSummary.balance) }}
            </div>
            <div class="stat-label">Balance {{ accountSummary.overspending ? '(OVER)' : '' }}</div>
          </div>
        </div>

        <!-- New Transaction Form -->
        <div class="card mb-2" style="border: 1px solid var(--color-border);">
          <div class="card-header">New Transaction</div>
          <div class="card-body">
            <div v-if="txnError" class="alert alert-error">{{ txnError }}</div>
            <div v-if="txnSuccess" class="alert alert-success">{{ txnSuccess }}</div>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
              <div class="form-group">
                <label>Type</label>
                <select v-model="txnForm.type" class="form-control">
                  <option value="expense">Expense</option>
                  <option value="income">Income</option>
                </select>
              </div>
              <div class="form-group">
                <label>Amount</label>
                <input v-model.number="txnForm.amount" type="number" step="0.01" min="0.01" class="form-control" placeholder="0.00" />
              </div>
              <div class="form-group">
                <label>Category</label>
                <input v-model="txnForm.category" type="text" class="form-control" placeholder="e.g. travel, materials" />
              </div>
              <div class="form-group">
                <label>Description</label>
                <input v-model="txnForm.description" type="text" class="form-control" placeholder="Transaction description" />
              </div>
            </div>
            <button class="btn btn-primary" :disabled="txnSubmitting" @click="createTransaction">
              <span v-if="txnSubmitting" class="spinner"></span>
              <span v-else>Record Transaction</span>
            </button>
          </div>
        </div>

        <!-- Transactions List -->
        <h3 class="mb-1">Transaction History</h3>
        <div v-if="loadingTxns" class="text-center"><span class="spinner"></span></div>
        <table v-else-if="transactions.length > 0">
          <thead>
            <tr>
              <th>Date</th>
              <th>Type</th>
              <th>Amount</th>
              <th>Category</th>
              <th>Description</th>
              <th>Invoice</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="txn in transactions" :key="txn.id">
              <td class="text-muted">{{ formatDate(txn.recorded_at) }}</td>
              <td>
                <span :class="txn.type === 'income' ? 'text-success' : 'text-danger'">
                  {{ txn.type }}
                </span>
              </td>
              <td>{{ formatCurrency(txn.amount) }}</td>
              <td>{{ txn.category || '--' }}</td>
              <td>{{ txn.description || '--' }}</td>
              <td>
                <div v-if="txn.has_invoice" class="flex items-center gap-1">
                  <span class="text-success" style="font-size: 12px;">&#10003; Attached</span>
                  <button
                    class="btn btn-sm btn-outline"
                    style="font-size: 11px; padding: 2px 6px;"
                    :disabled="invoiceDownloading[txn.id]"
                    data-test="download-invoice"
                    @click="downloadInvoice(txn)"
                  >
                    <span v-if="invoiceDownloading[txn.id]" class="spinner" style="width: 12px; height: 12px;"></span>
                    <span v-else>View</span>
                  </button>
                </div>
                <label v-else class="btn btn-sm btn-outline" style="cursor: pointer; font-size: 11px; padding: 2px 6px;">
                  Upload
                  <input
                    type="file"
                    accept=".pdf,.jpg,.jpeg,.png"
                    style="display: none;"
                    @change="uploadInvoice(txn, $event)"
                  />
                </label>
                <span v-if="invoiceUploading[txn.id]" class="spinner" style="width: 12px; height: 12px;"></span>
                <div v-if="invoiceErrors[txn.id]" class="text-danger" style="font-size: 11px;">{{ invoiceErrors[txn.id] }}</div>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-muted">No transactions recorded.</p>
      </div>
    </div>

    <!-- Create Account Modal -->
    <div v-if="showCreateAccount" class="modal-overlay" @click.self="showCreateAccount = false">
      <div class="modal">
        <h3>Create Funding Account</h3>
        <div v-if="createAccountError" class="alert alert-error">{{ createAccountError }}</div>
        <div class="form-group">
          <label>Account Name</label>
          <input v-model="newAccount.name" type="text" class="form-control" placeholder="Account name" />
        </div>
        <div class="form-group">
          <label>Registration</label>
          <select v-model="newAccount.registration_id" class="form-control">
            <option value="">Select a registration...</option>
            <option v-for="reg in registrations" :key="reg.id" :value="reg.id">
              {{ reg.title || '(Untitled)' }} &ndash; {{ reg.status }}
            </option>
          </select>
        </div>
        <div class="form-group">
          <label>Allocated Budget</label>
          <input v-model.number="newAccount.allocated_budget" type="number" step="0.01" min="0" class="form-control" placeholder="0.00" />
        </div>
        <div class="modal-actions">
          <button class="btn btn-outline" @click="showCreateAccount = false">Cancel</button>
          <button class="btn btn-primary" :disabled="creatingAccount" @click="createAccount">
            <span v-if="creatingAccount" class="spinner"></span>
            <span v-else>Create</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Over-Budget Confirmation Modal -->
    <div v-if="showOverBudgetDialog" class="modal-overlay">
      <div class="modal">
        <h3 style="color: var(--color-danger);">Over-Budget Warning</h3>
        <p>This transaction would push expenses over the allocated budget.</p>
        <div v-if="overBudgetInfo" class="mt-1">
          <p><strong>Projected Total Expenses:</strong> {{ formatCurrency(overBudgetInfo.current_total_expenses) }}</p>
          <p><strong>Allocated Budget:</strong> {{ formatCurrency(overBudgetInfo.allocated_budget) }}</p>
          <p><strong>Over by:</strong> {{ overBudgetInfo.overage_pct }}%</p>
        </div>
        <p class="mt-1">Are you sure you want to proceed?</p>
        <div class="modal-actions">
          <button class="btn btn-outline" @click="showOverBudgetDialog = false; overBudgetInfo = null;">
            Cancel
          </button>
          <button class="btn btn-danger" :disabled="txnSubmitting" @click="confirmOverBudget">
            Confirm Over-Budget Transaction
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'FinanceDashboardPage',
  data() {
    return {
      stats: null,
      accounts: [],
      loadingAccounts: false,
      selectedAccount: null,
      accountSummary: null,
      transactions: [],
      loadingTxns: false,
      txnForm: {
        type: 'expense',
        amount: null,
        category: '',
        description: '',
      },
      txnSubmitting: false,
      txnError: '',
      txnSuccess: '',
      showCreateAccount: false,
      newAccount: {
        name: '',
        registration_id: '',
        allocated_budget: 0,
      },
      creatingAccount: false,
      createAccountError: '',
      invoiceUploading: {},
      invoiceErrors: {},
      invoiceDownloading: {},
      statsError: '',
      registrationsError: '',
      accountsError: '',
      summaryError: '',
      txnsError: '',
      showOverBudgetDialog: false,
      overBudgetInfo: null,
      registrations: [],
      focusedRegistrationId: '',
      statsCategory: '',
      statsFrom: '',
      statsTo: '',
    }
  },
  computed: {
    filteredAccounts() {
      if (!this.focusedRegistrationId) return this.accounts
      return this.accounts.filter(a => a.registration_id === this.focusedRegistrationId)
    },
  },
  methods: {
    formatDate(dateStr) {
      if (!dateStr) return '--'
      return new Date(dateStr).toLocaleString()
    },
    formatCurrency(val) {
      if (val == null) return '--'
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val)
    },
    async loadStats() {
      this.statsError = ''
      try {
        const params = {}
        if (this.statsCategory) params.category = this.statsCategory
        if (this.statsFrom) params.from_date = this.statsFrom
        if (this.statsTo) params.to_date = this.statsTo
        const res = await api.get('/finance/statistics', { params })
        this.stats = res.data
      } catch (err) {
        this.statsError = err.response?.data?.detail
          || 'Failed to load finance statistics.'
      }
    },
    async loadRegistrations() {
      this.registrationsError = ''
      try {
        const res = await api.get('/registrations', { params: { page_size: 100 } })
        this.registrations = res.data.items
      } catch (err) {
        this.registrations = []
        this.registrationsError = err.response?.data?.detail
          || 'Failed to load registrations for the account form.'
      }
    },
    registrationLabel(regId) {
      const reg = this.registrations.find(r => r.id === regId)
      return reg ? `${reg.title || '(Untitled)'} – ${reg.status}` : regId
    },
    async loadAccounts() {
      this.loadingAccounts = true
      this.accountsError = ''
      try {
        const res = await api.get('/finance/accounts')
        this.accounts = res.data
      } catch (err) {
        this.accounts = []
        this.accountsError = err.response?.data?.detail
          || 'Failed to load funding accounts.'
      } finally {
        this.loadingAccounts = false
      }
    },
    async selectAccount(acct) {
      this.selectedAccount = acct
      this.txnError = ''
      this.txnSuccess = ''
      this.txnForm = { type: 'expense', amount: null, category: '', description: '' }
      await Promise.all([this.loadAccountSummary(acct.id), this.loadTransactions(acct.id)])
    },
    async loadAccountSummary(accountId) {
      this.summaryError = ''
      try {
        const res = await api.get(`/finance/accounts/${accountId}`)
        this.accountSummary = res.data
      } catch (err) {
        this.accountSummary = null
        this.summaryError = err.response?.data?.detail
          || 'Failed to load account summary.'
      }
    },
    async loadTransactions(accountId) {
      this.loadingTxns = true
      this.txnsError = ''
      try {
        const res = await api.get(`/finance/accounts/${accountId}/transactions`)
        this.transactions = res.data
      } catch (err) {
        this.transactions = []
        this.txnsError = err.response?.data?.detail
          || 'Failed to load transactions.'
      } finally {
        this.loadingTxns = false
      }
    },
    async createTransaction(overBudgetConfirmed = false) {
      if (!this.selectedAccount) return
      if (!this.txnForm.amount || this.txnForm.amount <= 0) {
        this.txnError = 'Amount must be greater than 0.'
        return
      }
      if (!this.txnForm.category || !this.txnForm.category.trim()) {
        this.txnError = 'Category is required.'
        return
      }

      this.txnSubmitting = true
      this.txnError = ''
      this.txnSuccess = ''

      try {
        const payload = {
          type: this.txnForm.type,
          amount: this.txnForm.amount,
          category: this.txnForm.category || null,
          description: this.txnForm.description || null,
          over_budget_confirmed: overBudgetConfirmed === true,
        }
        await api.post(`/finance/accounts/${this.selectedAccount.id}/transactions`, payload)
        this.txnSuccess = 'Transaction recorded successfully.'
        this.txnForm = { type: 'expense', amount: null, category: '', description: '' }
        await Promise.all([
          this.loadAccountSummary(this.selectedAccount.id),
          this.loadTransactions(this.selectedAccount.id),
          this.loadStats(),
        ])
      } catch (err) {
        if (err.response?.status === 409) {
          // Over-budget confirmation needed
          this.overBudgetInfo = err.response.data.detail
          this.showOverBudgetDialog = true
        } else {
          this.txnError = err.response?.data?.detail || 'Failed to create transaction.'
        }
      } finally {
        this.txnSubmitting = false
      }
    },
    async confirmOverBudget() {
      this.showOverBudgetDialog = false
      this.overBudgetInfo = null
      await this.createTransaction(true)
    },
    async createAccount() {
      this.creatingAccount = true
      this.createAccountError = ''
      try {
        await api.post('/finance/accounts', {
          name: this.newAccount.name,
          registration_id: this.newAccount.registration_id,
          allocated_budget: this.newAccount.allocated_budget,
        })
        this.showCreateAccount = false
        this.newAccount = { name: '', registration_id: '', allocated_budget: 0 }
        await this.loadAccounts()
      } catch (err) {
        this.createAccountError = err.response?.data?.detail || 'Failed to create account.'
      } finally {
        this.creatingAccount = false
      }
    },
    async uploadInvoice(txn, event) {
      const file = event.target.files[0]
      if (!file) return

      const allowed = ['application/pdf', 'image/jpeg', 'image/png']
      const allowedExts = ['.pdf', '.jpg', '.jpeg', '.png']
      const ext = '.' + file.name.split('.').pop().toLowerCase()
      if (!allowedExts.includes(ext) && !allowed.includes(file.type)) {
        this.invoiceErrors = { ...this.invoiceErrors, [txn.id]: 'File type not allowed. Use PDF, JPG, or PNG.' }
        event.target.value = ''
        return
      }
      if (file.size > 20 * 1024 * 1024) {
        this.invoiceErrors = { ...this.invoiceErrors, [txn.id]: 'File exceeds 20 MB.' }
        event.target.value = ''
        return
      }

      this.invoiceUploading = { ...this.invoiceUploading, [txn.id]: true }
      this.invoiceErrors = { ...this.invoiceErrors, [txn.id]: '' }

      try {
        const formData = new FormData()
        formData.append('file', file, file.name)
        const res = await api.post(
          `/finance/transactions/${txn.id}/invoice`,
          formData,
          { headers: { 'Content-Type': 'multipart/form-data' } }
        )
        const idx = this.transactions.findIndex(t => t.id === txn.id)
        if (idx !== -1) {
          this.transactions[idx] = res.data
        }
      } catch (err) {
        this.invoiceErrors = { ...this.invoiceErrors, [txn.id]: err.response?.data?.detail || 'Invoice upload failed.' }
      } finally {
        this.invoiceUploading = { ...this.invoiceUploading, [txn.id]: false }
        event.target.value = ''
      }
    },
    async downloadInvoice(txn) {
      this.invoiceErrors = { ...this.invoiceErrors, [txn.id]: '' }
      this.invoiceDownloading = { ...this.invoiceDownloading, [txn.id]: true }
      try {
        const res = await api.get(
          `/finance/transactions/${txn.id}/invoice`,
          { responseType: 'blob' }
        )
        const blob = new Blob([res.data], {
          type: res.headers?.['content-type'] || 'application/octet-stream',
        })
        const url = window.URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `invoice_${txn.id}`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        window.URL.revokeObjectURL(url)
      } catch (err) {
        let detail = 'Invoice download failed.'
        if (err.response?.data instanceof Blob) {
          try {
            const text = await err.response.data.text()
            const parsed = JSON.parse(text)
            detail = parsed.detail || detail
          } catch {
            // leave default
          }
        } else if (err.response?.data?.detail) {
          detail = err.response.data.detail
        }
        this.invoiceErrors = { ...this.invoiceErrors, [txn.id]: detail }
      } finally {
        this.invoiceDownloading = { ...this.invoiceDownloading, [txn.id]: false }
      }
    },
  },
  async mounted() {
    await Promise.all([this.loadStats(), this.loadAccounts(), this.loadRegistrations()])
    // If arriving from the registration list with a pre-selected registration,
    // filter the account list to that registration and auto-select the existing
    // account if one exists; otherwise open the create-account modal pre-filled.
    const regId = this.$route?.query?.registration_id
    if (regId) {
      this.focusedRegistrationId = regId
      const existingAccount = this.accounts.find(a => a.registration_id === regId)
      if (existingAccount) {
        await this.selectAccount(existingAccount)
      } else {
        this.newAccount.registration_id = regId
        this.showCreateAccount = true
      }
    }
  },
}
</script>
