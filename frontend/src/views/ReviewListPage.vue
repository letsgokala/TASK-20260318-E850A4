<template>
  <div>
    <div class="page-header">
      <h1>Review Queue</h1>
      <p>Review submitted registrations. Use batch actions for efficiency.</p>
    </div>

    <div class="filters">
      <select v-model="statusFilter" class="form-control" @change="loadPage(1)">
        <option value="">All Statuses</option>
        <option value="submitted">Submitted</option>
        <option value="supplemented">Supplemented</option>
        <option value="approved">Approved</option>
        <option value="rejected">Rejected</option>
        <option value="waitlisted">Waitlisted</option>
        <option value="promoted_from_waitlist">Promoted</option>
      </select>

      <select v-model="selectedBatch" class="form-control" @change="loadPage(1)">
        <option value="">All Batches</option>
        <option v-for="batch in batches" :key="batch.id" :value="batch.id">
          {{ batch.name }}
        </option>
      </select>
    </div>

    <!-- Batch Actions -->
    <div v-if="selected.length > 0" class="flex items-center gap-1 mb-2">
      <span class="text-muted">{{ selected.length }} selected</span>
      <input
        v-model="batchComment"
        type="text"
        class="form-control"
        placeholder="Review comment..."
        style="width: auto; min-width: 200px; font-size: 13px;"
      />
      <button class="btn btn-sm btn-success" @click="batchAction('approved')" :disabled="batchLoading">
        Batch Approve
      </button>
      <button class="btn btn-sm btn-danger" @click="batchAction('rejected')" :disabled="batchLoading">
        Batch Reject
      </button>
      <button class="btn btn-sm btn-warning" @click="batchAction('waitlisted')" :disabled="batchLoading">
        Batch Waitlist
      </button>
      <span v-if="batchLoading" class="spinner ml-1"></span>
    </div>

    <div v-if="batchResult" class="alert" :class="batchResult.failed > 0 ? 'alert-warning' : 'alert-success'">
      Batch action: {{ batchResult.succeeded }} succeeded, {{ batchResult.failed }} failed.
    </div>

    <div v-if="loadError" class="alert alert-error" data-test="review-load-error">
      {{ loadError }}
      <button class="btn btn-sm btn-outline ml-1" @click="loadPage(page)">Retry</button>
    </div>

    <div v-if="batchesError" class="alert alert-warning" data-test="review-batches-error">
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
              <th style="width: 40px;">
                <input type="checkbox" @change="toggleAll" :checked="allSelected" />
              </th>
              <th>Title</th>
              <th>Activity Type</th>
              <th>Applicant</th>
              <th>Status</th>
              <th>Budget</th>
              <th>Updated</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="registrations.length === 0">
              <td colspan="8" class="text-center text-muted" style="padding: 24px">
                No registrations to review.
              </td>
            </tr>
            <tr v-for="reg in registrations" :key="reg.id">
              <td>
                <input type="checkbox" :value="reg.id" v-model="selected" />
              </td>
              <td>{{ reg.title || '(Untitled)' }}</td>
              <td>{{ reg.activity_type || '--' }}</td>
              <td>{{ reg.applicant_name || '--' }}</td>
              <td><span class="badge" :class="'badge-' + reg.status">{{ reg.status }}</span></td>
              <td>{{ reg.requested_budget != null ? formatCurrency(reg.requested_budget) : '--' }}</td>
              <td class="text-muted">{{ formatDate(reg.updated_at) }}</td>
              <td>
                <router-link :to="'/reviews/' + reg.id" class="btn btn-sm btn-primary">
                  Review
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
  name: 'ReviewListPage',
  data() {
    return {
      registrations: [],
      batches: [],
      loading: false,
      loadError: '',
      batchesError: '',
      page: 1,
      pageSize: 20,
      total: 0,
      statusFilter: 'submitted',
      selectedBatch: '',
      selected: [],
      batchLoading: false,
      batchResult: null,
      batchComment: '',
    }
  },
  computed: {
    totalPages() {
      return Math.max(1, Math.ceil(this.total / this.pageSize))
    },
    allSelected() {
      return this.registrations.length > 0 && this.selected.length === this.registrations.length
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
    toggleAll(e) {
      if (e.target.checked) {
        this.selected = this.registrations.map((r) => r.id)
      } else {
        this.selected = []
      }
    },
    async loadPage(pageNum) {
      this.page = pageNum
      this.loading = true
      this.selected = []
      this.batchResult = null
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
          || 'Failed to load review queue — please retry or contact an administrator.'
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
        // Batch filter is optional — surface the error but don't block the page.
        this.batchesError = err.response?.data?.detail
          || 'Failed to load batch filter options.'
      }
    },
    async batchAction(action) {
      if (this.selected.length === 0) return
      this.batchLoading = true
      this.batchResult = null

      try {
        const res = await api.post('/reviews/batch', {
          registration_ids: this.selected,
          action: action,
          comment: this.batchComment || `Batch ${action}`,
        })
        this.batchResult = res.data
        this.selected = []
        await this.loadPage(this.page)
      } catch (err) {
        const detail = err.response?.data?.detail
        if (detail && detail.results) {
          this.batchResult = detail
        } else {
          this.batchResult = { succeeded: 0, failed: this.selected.length }
        }
      } finally {
        this.batchLoading = false
      }
    },
  },
  mounted() {
    this.loadBatches()
    this.loadPage(1)
  },
}
</script>
