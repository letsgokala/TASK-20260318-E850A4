<template>
  <div>
    <div class="page-header flex justify-between items-center">
      <div>
        <h1>Registrations</h1>
        <p>View and manage activity registrations.</p>
      </div>
      <router-link
        v-if="canCreate"
        to="/registrations/new"
        class="btn btn-primary"
      >
        + New Registration
      </router-link>
    </div>

    <div class="filters">
      <select v-model="statusFilter" class="form-control" @change="loadPage(1)">
        <option value="">All Statuses</option>
        <option value="draft">Draft</option>
        <option value="submitted">Submitted</option>
        <option value="supplemented">Supplemented</option>
        <option value="approved">Approved</option>
        <option value="rejected">Rejected</option>
        <option value="waitlisted">Waitlisted</option>
        <option value="promoted_from_waitlist">Promoted</option>
        <option value="canceled">Canceled</option>
      </select>

      <select v-model="selectedBatch" class="form-control" @change="loadPage(1)">
        <option value="">All Batches</option>
        <option v-for="batch in batches" :key="batch.id" :value="batch.id">
          {{ batch.name }}
        </option>
      </select>
    </div>

    <div v-if="loadError" class="alert alert-error" data-test="registration-load-error">
      {{ loadError }}
      <button class="btn btn-sm btn-outline ml-1" @click="loadPage(page)">Retry</button>
    </div>

    <div v-if="batchesError" class="alert alert-warning" data-test="registration-batches-error">
      {{ batchesError }}
    </div>

    <div v-if="loading" class="text-center mt-2">
      <span class="spinner"></span>
    </div>

    <div v-else class="card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Activity Type</th>
              <th>Status</th>
              <th>Budget</th>
              <th>Updated</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="registrations.length === 0">
              <td colspan="6" class="text-center text-muted" style="padding: 24px">
                No registrations found.
              </td>
            </tr>
            <tr v-for="reg in registrations" :key="reg.id">
              <td>{{ reg.title || '(Untitled)' }}</td>
              <td>{{ reg.activity_type || '--' }}</td>
              <td><span class="badge" :class="'badge-' + reg.status">{{ reg.status }}</span></td>
              <td>{{ reg.requested_budget != null ? formatCurrency(reg.requested_budget) : '--' }}</td>
              <td class="text-muted">{{ formatDate(reg.updated_at) }}</td>
              <td>
                <router-link
                  v-if="reg.status === 'draft' && canCreate"
                  :to="'/registrations/' + reg.id + '/edit'"
                  class="btn btn-sm btn-outline"
                >
                  Edit
                </router-link>
                <router-link
                  v-else
                  :to="isReviewer ? '/reviews/' + reg.id : isFinanceAdmin ? '/finance?registration_id=' + reg.id : '/registrations/' + reg.id + '/edit'"
                  class="btn btn-sm btn-outline"
                >
                  View
                </router-link>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="pagination" v-if="totalPages > 1">
        <button class="btn btn-sm btn-outline" :disabled="page <= 1" @click="loadPage(page - 1)">
          Previous
        </button>
        <span>Page {{ page }} of {{ totalPages }}</span>
        <button class="btn btn-sm btn-outline" :disabled="page >= totalPages" @click="loadPage(page + 1)">
          Next
        </button>
      </div>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'RegistrationListPage',
  data() {
    return {
      role: localStorage.getItem('userRole') || '',
      registrations: [],
      batches: [],
      loading: false,
      loadError: '',
      batchesError: '',
      page: 1,
      pageSize: 20,
      total: 0,
      statusFilter: '',
      selectedBatch: '',
    }
  },
  computed: {
    totalPages() {
      return Math.max(1, Math.ceil(this.total / this.pageSize))
    },
    canCreate() {
      return ['applicant', 'system_admin'].includes(this.role)
    },
    isReviewer() {
      return ['reviewer', 'system_admin'].includes(this.role)
    },
    isFinanceAdmin() {
      return this.role === 'financial_admin'
    },
  },
  methods: {
    formatDate(dateStr) {
      if (!dateStr) return '--'
      return new Date(dateStr).toLocaleDateString()
    },
    formatCurrency(val) {
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val)
    },
    async loadPage(pageNum) {
      this.page = pageNum
      this.loading = true
      this.loadError = ''
      try {
        const params = { page: this.page, page_size: this.pageSize }
        if (this.statusFilter) params.status = this.statusFilter
        if (this.selectedBatch) params.batch_id = this.selectedBatch

        const res = await api.get('/registrations', { params })
        this.registrations = res.data.items
        this.total = res.data.total
      } catch (err) {
        this.registrations = []
        this.loadError = err.response?.data?.detail
          || 'Failed to load registrations. Check your connection and retry.'
      } finally {
        this.loading = false
      }
    },
    async loadBatches() {
      this.batchesError = ''
      try {
        const res = await api.get('/batches')
        this.batches = res.data
      } catch (err) {
        this.batchesError = err.response?.data?.detail
          || 'Failed to load batch filter options.'
      }
    },
  },
  mounted() {
    this.loadBatches()
    this.loadPage(1)
  },
}
</script>
