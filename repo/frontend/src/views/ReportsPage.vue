<template>
  <div>
    <div class="page-header">
      <h1>Reports &amp; Exports</h1>
      <p>Generate and download reconciliation, audit, compliance, and whitelist reports.</p>
    </div>

    <div v-if="generateError" class="alert alert-error mb-2">{{ generateError }}</div>

    <!-- Report Generation -->
    <div class="card mb-2">
      <div class="card-header">Generate Report</div>
      <div class="card-body">
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
          <div class="form-group">
            <label>Report Type *</label>
            <select v-model="form.report_type" class="form-control">
              <option value="reconciliation">Reconciliation</option>
              <option v-if="isSystemAdmin" value="audit">Audit</option>
              <option v-if="isSystemAdmin" value="compliance">Compliance</option>
              <option v-if="isSystemAdmin" value="whitelist">Whitelist</option>
            </select>
          </div>
          <div class="form-group">
            <label>Batch ID (optional)</label>
            <input v-model="form.batch_id" type="text" class="form-control" placeholder="UUID" />
          </div>
          <div class="form-group">
            <label>From Date</label>
            <input v-model="form.from_date" type="datetime-local" class="form-control" />
          </div>
          <div class="form-group">
            <label>To Date</label>
            <input v-model="form.to_date" type="datetime-local" class="form-control" />
          </div>
        </div>
        <button class="btn btn-primary" :disabled="generating" @click="generateReport">
          <span v-if="generating" class="spinner"></span>
          <span v-else>Generate Report</span>
        </button>
      </div>
    </div>

    <!-- Active / Recent Tasks -->
    <div class="card mb-2">
      <div class="card-header flex justify-between items-center">
        <span>Report Tasks</span>
        <button class="btn btn-sm btn-outline" @click="loadTasks">Refresh</button>
      </div>
      <div class="card-body">
        <div v-if="loadingTasks" class="text-center"><span class="spinner"></span></div>
        <table v-else-if="tasks.length > 0">
          <thead>
            <tr>
              <th>Type</th>
              <th>Status</th>
              <th>Created</th>
              <th>Completed</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td>{{ task.report_type }}</td>
              <td>
                <span :class="statusClass(task.status)">{{ task.status }}</span>
              </td>
              <td class="text-muted">{{ formatDate(task.created_at) }}</td>
              <td class="text-muted">{{ task.completed_at ? formatDate(task.completed_at) : '--' }}</td>
              <td>
                <button
                  v-if="task.status === 'complete'"
                  class="btn btn-sm btn-primary"
                  :disabled="downloading[task.id]"
                  @click="downloadReport(task)"
                >
                  <span v-if="downloading[task.id]" class="spinner"></span>
                  <span v-else>Download</span>
                </button>
                <span v-else-if="task.status === 'processing' || task.status === 'pending'" class="text-muted" style="font-size: 12px;">
                  In progress...
                </span>
                <span v-else-if="task.status === 'failed'" class="text-danger" style="font-size: 12px;">
                  {{ task.error_message || 'Failed' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-muted">No report tasks yet. Generate a report above.</p>
      </div>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'ReportsPage',
  data() {
    return {
      role: localStorage.getItem('userRole') || '',
      form: {
        report_type: 'reconciliation',
        batch_id: '',
        from_date: '',
        to_date: '',
      },
      generating: false,
      generateError: '',
      tasks: [],
      loadingTasks: false,
      downloading: {},
      pollTimer: null,
    }
  },
  computed: {
    isSystemAdmin() {
      return this.role === 'system_admin'
    },
  },
  methods: {
    formatDate(d) {
      if (!d) return '--'
      return new Date(d).toLocaleString()
    },
    statusClass(s) {
      if (s === 'complete') return 'text-success'
      if (s === 'failed') return 'text-danger'
      return 'text-muted'
    },
    async generateReport() {
      this.generating = true
      this.generateError = ''
      try {
        const params = new URLSearchParams()
        params.set('report_type', this.form.report_type) // used in path
        if (this.form.batch_id) params.set('batch_id', this.form.batch_id)
        if (this.form.from_date) params.set('from_date', new Date(this.form.from_date).toISOString())
        if (this.form.to_date) params.set('to_date', new Date(this.form.to_date).toISOString())

        const queryString = params.toString().replace(/^report_type=[^&]+(&|$)/, '').replace(/^&/, '')
        const url = `/reports/generate/${this.form.report_type}${queryString ? '?' + queryString : ''}`
        const res = await api.post(url)
        this.tasks.unshift(res.data)
        this.startPolling()
      } catch (err) {
        this.generateError = err.response?.data?.detail || 'Failed to generate report.'
      } finally {
        this.generating = false
      }
    },
    async loadTasks() {
      this.loadingTasks = true
      try {
        // Load all tasks from backend so history survives page refresh
        const res = await api.get('/reports/tasks')
        this.tasks = res.data

        // Re-fetch fresh status for any pending/processing tasks
        const updated = []
        for (const task of this.tasks) {
          if (task.status === 'pending' || task.status === 'processing') {
            try {
              const refreshed = await api.get(`/reports/tasks/${task.id}`)
              updated.push(refreshed.data)
            } catch {
              updated.push(task)
            }
          } else {
            updated.push(task)
          }
        }
        this.tasks = updated
      } catch {
        // silently keep existing task list
      } finally {
        this.loadingTasks = false
      }
    },
    async downloadReport(task) {
      this.downloading = { ...this.downloading, [task.id]: true }
      try {
        const res = await api.get(`/reports/tasks/${task.id}/download`, {
          responseType: 'blob',
        })
        const blob = new Blob([res.data], {
          type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${task.report_type}_${task.id}.xlsx`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
      } catch (err) {
        alert('Download failed: ' + (err.response?.data?.detail || 'Unknown error'))
      } finally {
        this.downloading = { ...this.downloading, [task.id]: false }
      }
    },
    startPolling() {
      if (this.pollTimer) return
      this.pollTimer = setInterval(() => {
        const hasPending = this.tasks.some(
          t => t.status === 'pending' || t.status === 'processing'
        )
        if (hasPending) {
          this.loadTasks()
        } else {
          clearInterval(this.pollTimer)
          this.pollTimer = null
        }
      }, 5000)
    },
  },
  mounted() {
    // Load prior tasks on mount so history survives page refresh
    this.loadTasks()
  },
  beforeUnmount() {
    if (this.pollTimer) clearInterval(this.pollTimer)
  },
}
</script>
