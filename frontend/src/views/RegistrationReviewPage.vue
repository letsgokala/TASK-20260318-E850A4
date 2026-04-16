<template>
  <div>
    <div class="page-header flex justify-between items-center">
      <div>
        <h1>Registration Review</h1>
        <p v-if="registration">{{ registration.title || '(Untitled)' }}</p>
      </div>
      <router-link to="/reviews" class="btn btn-outline">Back to Reviews</router-link>
    </div>

    <div v-if="loading" class="text-center mt-3">
      <span class="spinner"></span>
    </div>

    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div v-if="registration && !loading">
      <!-- Registration Details -->
      <div class="card mb-2">
        <div class="card-header flex justify-between items-center">
          <span>Registration Details</span>
          <span class="badge" :class="'badge-' + registration.status">{{ registration.status }}</span>
        </div>
        <div class="card-body">
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
            <div><strong>Title:</strong> {{ registration.title || '--' }}</div>
            <div><strong>Activity Type:</strong> {{ registration.activity_type || '--' }}</div>
            <div><strong>Applicant:</strong> {{ registration.applicant_name || '--' }}</div>
            <div><strong>Budget:</strong> {{ registration.requested_budget != null ? '$' + registration.requested_budget : '--' }}</div>
            <div><strong>Start Date:</strong> {{ formatDate(registration.start_date) }}</div>
            <div><strong>End Date:</strong> {{ formatDate(registration.end_date) }}</div>
          </div>
          <div class="mt-1">
            <strong>Description:</strong>
            <p>{{ registration.description || '--' }}</p>
          </div>
        </div>
      </div>

      <!-- Materials -->
      <div class="card mb-2">
        <div class="card-header">Materials</div>
        <div class="card-body">
          <div v-if="materials.length === 0" class="text-muted">No materials uploaded.</div>
          <div v-for="mat in materials" :key="mat.id" class="material-review-item">
            <strong>Checklist: {{ checklistLabel(mat.checklist_item_id) }}</strong>
            <div v-for="ver in mat.versions" :key="ver.id" class="flex items-center gap-1 mt-1" style="padding: 6px 0; border-bottom: 1px solid var(--color-border);">
              <span>v{{ ver.version_number }}: {{ ver.original_filename }}</span>
              <span class="badge" :class="'badge-' + ver.status">{{ ver.status }}</span>
              <button
                class="btn btn-sm btn-outline"
                :disabled="downloadingVersion[ver.id]"
                :title="'Download ' + ver.original_filename"
                data-test="download-version"
                @click="downloadVersion(ver)"
              >
                <span v-if="downloadingVersion[ver.id]" class="spinner"></span>
                <span v-else>Download</span>
              </button>
              <select
                v-model="versionStatusUpdates[ver.id]"
                class="form-control"
                style="width: auto; min-width: 140px; font-size: 12px; padding: 4px 8px;"
              >
                <option value="">Change status...</option>
                <option value="submitted">Submitted</option>
                <option value="needs_correction">Needs Correction</option>
              </select>
              <input
                v-if="versionStatusUpdates[ver.id] === 'needs_correction'"
                v-model="correctionReasons[ver.id]"
                type="text"
                class="form-control"
                placeholder="Reason for correction"
                style="width: auto; min-width: 200px; font-size: 12px; padding: 4px 8px;"
              />
              <button
                class="btn btn-sm btn-primary"
                :disabled="!versionStatusUpdates[ver.id] || updatingVersion[ver.id]"
                @click="updateVersionStatus(ver)"
              >
                Update
              </button>
            </div>
            <div v-if="downloadError[mat.id]" class="alert alert-error mt-1">
              {{ downloadError[mat.id] }}
            </div>
          </div>
        </div>
      </div>

      <!-- Review History -->
      <div class="card mb-2">
        <div class="card-header">Review History</div>
        <div class="card-body">
          <div v-if="history.length === 0" class="text-muted">No review history.</div>
          <div v-for="record in history" :key="record.id" style="padding: 8px 0; border-bottom: 1px solid var(--color-border);">
            <span class="badge" :class="'badge-' + record.from_status">{{ record.from_status }}</span>
            &rarr;
            <span class="badge" :class="'badge-' + record.to_status">{{ record.to_status }}</span>
            <span class="text-muted ml-1" style="font-size: 12px;">
              by {{ record.reviewed_by }} at {{ formatDate(record.reviewed_at) }}
            </span>
            <span v-if="record.comment" class="ml-1" style="font-size: 12px;">"{{ record.comment }}"</span>
          </div>
        </div>
      </div>

      <!-- Transition Actions -->
      <div class="card mb-2">
        <div class="card-header">Transition Actions</div>
        <div class="card-body">
          <div v-if="allowedTransitions.length === 0" class="text-muted">
            No transitions available for this registration in its current status.
          </div>
          <div v-else>
            <div class="form-group">
              <label>Comment</label>
              <textarea v-model="transitionComment" class="form-control" rows="2" placeholder="Add a review comment..."></textarea>
            </div>
            <div class="flex gap-1 flex-wrap">
              <button
                v-for="target in allowedTransitions"
                :key="target"
                class="btn btn-sm"
                :class="transitionButtonClass(target)"
                :disabled="transitioning"
                @click="performTransition(target)"
              >
                {{ target }}
              </button>
            </div>
          </div>

          <div v-if="transitionError" class="alert alert-error mt-1">{{ transitionError }}</div>
          <div v-if="transitionSuccess" class="alert alert-success mt-1">{{ transitionSuccess }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'RegistrationReviewPage',
  data() {
    return {
      loading: false,
      error: '',
      registration: null,
      materials: [],
      checklistItems: [],
      history: [],
      allowedTransitions: [],
      transitionComment: '',
      transitioning: false,
      transitionError: '',
      transitionSuccess: '',
      versionStatusUpdates: {},
      correctionReasons: {},
      updatingVersion: {},
      downloadingVersion: {},
      downloadError: {},
    }
  },
  methods: {
    checklistLabel(itemId) {
      const item = this.checklistItems.find(i => i.id === itemId)
      return item ? item.label : itemId
    },
    formatDate(dateStr) {
      if (!dateStr) return '--'
      return new Date(dateStr).toLocaleString()
    },
    transitionButtonClass(target) {
      const map = {
        approved: 'btn-success',
        rejected: 'btn-danger',
        waitlisted: 'btn-warning',
        promoted_from_waitlist: 'btn-success',
        canceled: 'btn-outline',
      }
      return map[target] || 'btn-primary'
    },
    async loadData() {
      this.loading = true
      this.error = ''
      const id = this.$route.params.id

      try {
        const [regRes, matRes, histRes, transRes] = await Promise.all([
          api.get(`/registrations/${id}`),
          api.get(`/registrations/${id}/materials`),
          api.get(`/reviews/registrations/${id}/history`),
          api.get(`/reviews/registrations/${id}/allowed-transitions`),
        ])

        this.registration = regRes.data
        this.materials = matRes.data
        this.history = histRes.data
        this.allowedTransitions = transRes.data

        // Load checklist items for label display
        if (regRes.data.batch_id) {
          try {
            const clRes = await api.get(`/batches/${regRes.data.batch_id}/checklist`)
            this.checklistItems = clRes.data
          } catch {
            // silently fail — labels will fall back to item ID
          }
        }
      } catch (err) {
        this.error = err.response?.data?.detail || 'Failed to load registration.'
      } finally {
        this.loading = false
      }
    },
    async performTransition(toStatus) {
      this.transitioning = true
      this.transitionError = ''
      this.transitionSuccess = ''
      const id = this.$route.params.id

      try {
        await api.post(`/reviews/registrations/${id}/transition`, {
          to_status: toStatus,
          comment: this.transitionComment || null,
        })
        this.transitionSuccess = `Registration transitioned to "${toStatus}".`
        this.transitionComment = ''
        await this.loadData()
      } catch (err) {
        const detail = err.response?.data?.detail
        if (typeof detail === 'object' && detail.message) {
          this.transitionError = detail.message
        } else {
          this.transitionError = detail || 'Transition failed.'
        }
      } finally {
        this.transitioning = false
      }
    },
    async downloadVersion(ver) {
      // Clear any previous material-level error for this material
      this.downloadError = { ...this.downloadError, [ver.material_id]: '' }
      this.downloadingVersion = { ...this.downloadingVersion, [ver.id]: true }
      try {
        const res = await api.get(
          `/registrations/versions/${ver.id}/download`,
          { responseType: 'blob' }
        )
        // Turn the blob into a download in the browser. Using a same-origin
        // object URL keeps the JWT-bearing request in the XHR call; the
        // browser is only handed a blob reference, never the token.
        const blob = new Blob([res.data], {
          type: res.headers?.['content-type'] || 'application/octet-stream',
        })
        const url = window.URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = ver.original_filename || `version-${ver.id}`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        window.URL.revokeObjectURL(url)
      } catch (err) {
        let detail = 'Failed to download material.'
        // Blob error payloads need to be parsed back to text to extract detail
        if (err.response?.data instanceof Blob) {
          try {
            const text = await err.response.data.text()
            const parsed = JSON.parse(text)
            detail = parsed.detail || detail
          } catch {
            // leave default detail
          }
        } else if (err.response?.data?.detail) {
          detail = err.response.data.detail
        }
        this.downloadError = { ...this.downloadError, [ver.material_id]: detail }
      } finally {
        this.downloadingVersion = { ...this.downloadingVersion, [ver.id]: false }
      }
    },
    async updateVersionStatus(ver) {
      const newStatus = this.versionStatusUpdates[ver.id]
      if (!newStatus) return

      this.updatingVersion[ver.id] = true
      try {
        const payload = { status: newStatus }
        if (newStatus === 'needs_correction') {
          payload.correction_reason = this.correctionReasons[ver.id] || 'Correction needed'
        }

        await api.put(`/registrations/versions/${ver.id}/status`, payload)
        this.versionStatusUpdates[ver.id] = ''
        this.correctionReasons[ver.id] = ''
        await this.loadData()
      } catch (err) {
        alert(err.response?.data?.detail || 'Failed to update material status.')
      } finally {
        this.updatingVersion[ver.id] = false
      }
    },
  },
  mounted() {
    this.loadData()
  },
}
</script>
