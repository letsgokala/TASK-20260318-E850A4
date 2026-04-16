<template>
  <div>
    <div class="page-header">
      <h1>Dashboard</h1>
      <p>Welcome back. Here is your overview.</p>
    </div>

    <div v-if="loading" class="text-center mt-3">
      <span class="spinner"></span>
    </div>

    <div v-if="loadError" class="alert alert-error" data-test="dashboard-load-error">
      {{ loadError }}
    </div>

    <!-- Applicant Dashboard -->
    <div v-if="role === 'applicant'">
      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-value">{{ stats.totalRegistrations }}</div>
          <div class="stat-label">My Registrations</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.draftCount }}</div>
          <div class="stat-label">Drafts</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.submittedCount }}</div>
          <div class="stat-label">Submitted</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.approvedCount }}</div>
          <div class="stat-label">Approved</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">Recent Registrations</div>
        <div class="card-body">
          <table v-if="recentRegistrations.length > 0">
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="reg in recentRegistrations" :key="reg.id">
                <td>
                  <router-link :to="'/registrations/' + reg.id + '/edit'">
                    {{ reg.title || '(Untitled Draft)' }}
                  </router-link>
                </td>
                <td><span class="badge" :class="'badge-' + reg.status">{{ reg.status }}</span></td>
                <td class="text-muted">{{ formatDate(reg.updated_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="text-muted">No registrations yet.</p>
        </div>
      </div>
    </div>

    <!-- Reviewer Dashboard -->
    <div v-if="role === 'reviewer' || role === 'system_admin'">
      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-value">{{ stats.totalRegistrations }}</div>
          <div class="stat-label">Total Registrations</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.pendingReview }}</div>
          <div class="stat-label">Pending Review</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.approvedCount }}</div>
          <div class="stat-label">Approved</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.rejectedCount }}</div>
          <div class="stat-label">Rejected</div>
        </div>
      </div>

      <div v-if="metricsData" class="card mb-2">
        <div class="card-header">Metrics</div>
        <div class="card-body">
          <div class="stats-row">
            <div class="stat-card">
              <div class="stat-value" :class="{ 'text-danger': metricsData.approval_rate?.breached }">
                {{ metricsData.approval_rate?.value ?? '--' }}%
              </div>
              <div class="stat-label">Approval Rate</div>
            </div>
            <div class="stat-card">
              <div class="stat-value" :class="{ 'text-danger': metricsData.correction_rate?.breached }">
                {{ metricsData.correction_rate?.value ?? '--' }}%
              </div>
              <div class="stat-label">Correction Rate</div>
            </div>
            <div class="stat-card">
              <div class="stat-value" :class="{ 'text-danger': metricsData.overspending_rate?.breached }">
                {{ metricsData.overspending_rate?.value ?? '--' }}%
              </div>
              <div class="stat-label">Overspending Rate</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Financial Admin Dashboard -->
    <div v-if="role === 'financial_admin'">
      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-value">{{ stats.totalAccounts }}</div>
          <div class="stat-label">Funding Accounts</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ formatCurrency(stats.totalBudget) }}</div>
          <div class="stat-label">Total Budget</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ formatCurrency(stats.totalIncome) }}</div>
          <div class="stat-label">Total Income</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ formatCurrency(stats.totalExpense) }}</div>
          <div class="stat-label">Total Expense</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'DashboardPage',
  data() {
    return {
      role: localStorage.getItem('userRole') || '',
      loading: false,
      loadError: '',
      stats: {
        totalRegistrations: 0,
        draftCount: 0,
        submittedCount: 0,
        approvedCount: 0,
        rejectedCount: 0,
        pendingReview: 0,
        totalAccounts: 0,
        totalBudget: 0,
        totalIncome: 0,
        totalExpense: 0,
      },
      recentRegistrations: [],
      metricsData: null,
    }
  },
  methods: {
    formatDate(dateStr) {
      if (!dateStr) return '--'
      return new Date(dateStr).toLocaleDateString()
    },
    formatCurrency(val) {
      if (val == null) return '--'
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val)
    },
    async loadApplicantData() {
      try {
        const [allRes, draftRes, submittedRes, approvedRes] = await Promise.all([
          api.get('/registrations', { params: { page: 1, page_size: 5 } }),
          api.get('/registrations', { params: { status: 'draft', page: 1, page_size: 1 } }),
          api.get('/registrations', { params: { status: 'submitted', page: 1, page_size: 1 } }),
          api.get('/registrations', { params: { status: 'approved', page: 1, page_size: 1 } }),
        ])

        this.stats.totalRegistrations = allRes.data.total
        this.stats.draftCount = draftRes.data.total
        this.stats.submittedCount = submittedRes.data.total
        this.stats.approvedCount = approvedRes.data.total
        this.recentRegistrations = allRes.data.items
      } catch (err) {
        this.loadError = err.response?.data?.detail
          || 'Could not load your registration data. Try refreshing the page.'
      }
    },
    async loadReviewerData() {
      try {
        const [allRes, submittedRes, approvedRes, rejectedRes] = await Promise.all([
          api.get('/registrations', { params: { page: 1, page_size: 1 } }),
          api.get('/registrations', { params: { status: 'submitted', page: 1, page_size: 1 } }),
          api.get('/registrations', { params: { status: 'approved', page: 1, page_size: 1 } }),
          api.get('/registrations', { params: { status: 'rejected', page: 1, page_size: 1 } }),
        ])

        this.stats.totalRegistrations = allRes.data.total
        this.stats.pendingReview = submittedRes.data.total
        this.stats.approvedCount = approvedRes.data.total
        this.stats.rejectedCount = rejectedRes.data.total
      } catch (err) {
        this.loadError = err.response?.data?.detail
          || 'Could not load the review queue statistics. Try refreshing the page.'
      }

      try {
        const metricsRes = await api.get('/metrics')
        this.metricsData = metricsRes.data
      } catch (err) {
        // Metrics endpoint may 403 for financial_admin via this view; only
        // surface non-permission errors so the dashboard stays usable.
        if (err.response?.status && err.response.status !== 403) {
          this.loadError = err.response?.data?.detail
            || 'Could not load platform metrics.'
        }
      }
    },
    async loadFinanceData() {
      try {
        const [accountsRes, statsRes] = await Promise.all([
          api.get('/finance/accounts'),
          api.get('/finance/statistics'),
        ])

        this.stats.totalAccounts = accountsRes.data.length
        this.stats.totalBudget = accountsRes.data.reduce((sum, a) => sum + parseFloat(a.allocated_budget || 0), 0)
        this.stats.totalIncome = parseFloat(statsRes.data.grand_total_income || 0)
        this.stats.totalExpense = parseFloat(statsRes.data.grand_total_expense || 0)
      } catch (err) {
        this.loadError = err.response?.data?.detail
          || 'Could not load finance data. Try refreshing the page.'
      }
    },
  },
  async mounted() {
    this.loading = true
    if (this.role === 'applicant') {
      await this.loadApplicantData()
    } else if (this.role === 'reviewer' || this.role === 'system_admin') {
      await this.loadReviewerData()
      if (this.role === 'system_admin') {
        await this.loadFinanceData()
      }
    } else if (this.role === 'financial_admin') {
      await this.loadFinanceData()
      await this.loadReviewerData()
    }
    this.loading = false
  },
}
</script>
