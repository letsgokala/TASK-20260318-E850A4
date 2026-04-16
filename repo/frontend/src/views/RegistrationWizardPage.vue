<template>
  <div>
    <div class="page-header">
      <h1>{{ isEdit ? 'Edit Registration' : 'New Registration' }}</h1>
      <p v-if="autosaveMsg" class="text-muted">{{ autosaveMsg }}</p>
    </div>

    <div v-if="loadError" class="alert alert-error">{{ loadError }}</div>

    <div v-if="loading" class="text-center mt-3">
      <span class="spinner"></span>
    </div>

    <div v-else>
      <!-- Wizard Steps Indicator -->
      <div class="wizard-steps">
        <div
          v-for="(label, idx) in stepLabels"
          :key="idx"
          class="wizard-step"
          :class="{ active: currentStep === idx + 1, completed: currentStep > idx + 1 }"
          @click="goToStep(idx + 1)"
        >
          <span class="step-number">{{ currentStep > idx + 1 ? '&#10003;' : idx + 1 }}</span>
          {{ label }}
        </div>
      </div>

      <!-- Step 1: Basic Info -->
      <div v-show="currentStep === 1" class="card">
        <div class="card-header">Step 1: Basic Information</div>
        <div class="card-body">
          <div v-if="!isEdit" class="form-group">
            <label for="batch_id">Collection Batch *</label>
            <select id="batch_id" v-model="form.batch_id" class="form-control" required>
              <option value="">Select a batch...</option>
              <option v-for="batch in batches" :key="batch.id" :value="batch.id">
                {{ batch.name }}
              </option>
            </select>
          </div>

          <div class="form-group">
            <label for="title">Title *</label>
            <input id="title" v-model="form.title" type="text" class="form-control" placeholder="Activity title" />
          </div>

          <div class="form-group">
            <label for="activity_type">Activity Type *</label>
            <select id="activity_type" v-model="form.activity_type" class="form-control">
              <option value="">Select type...</option>
              <option value="workshop">Workshop</option>
              <option value="conference">Conference</option>
              <option value="training">Training</option>
              <option value="seminar">Seminar</option>
              <option value="research">Research</option>
              <option value="community_outreach">Community Outreach</option>
              <option value="other">Other</option>
            </select>
          </div>

          <div class="form-group">
            <label for="applicant_name">Applicant Name *</label>
            <input id="applicant_name" v-model="form.applicant_name" type="text" class="form-control" placeholder="Full name" />
          </div>
        </div>
      </div>

      <!-- Step 2: Details & Budget -->
      <div v-show="currentStep === 2" class="card">
        <div class="card-header">Step 2: Details &amp; Budget</div>
        <div class="card-body">
          <div class="form-group">
            <label for="description">Description *</label>
            <textarea id="description" v-model="form.description" class="form-control" rows="4" placeholder="Describe the activity..."></textarea>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
            <div class="form-group">
              <label for="start_date">Start Date</label>
              <input id="start_date" v-model="form.start_date" type="datetime-local" class="form-control" />
            </div>
            <div class="form-group">
              <label for="end_date">End Date</label>
              <input id="end_date" v-model="form.end_date" type="datetime-local" class="form-control" />
            </div>
          </div>

          <div class="form-group">
            <label for="requested_budget">Requested Budget</label>
            <input id="requested_budget" v-model.number="form.requested_budget" type="number" step="0.01" min="0" class="form-control" placeholder="0.00" />
          </div>

          <div class="form-group">
            <label for="applicant_email">Email</label>
            <input id="applicant_email" v-model="form.applicant_email" type="email" class="form-control" placeholder="email@example.com" />
          </div>

          <div class="form-group">
            <label for="applicant_phone">Phone</label>
            <input id="applicant_phone" v-model="form.applicant_phone" type="tel" class="form-control" placeholder="+1234567890" />
          </div>

          <div class="form-group">
            <label for="applicant_id_number">ID Number</label>
            <input id="applicant_id_number" v-model="form.applicant_id_number" type="text" class="form-control" placeholder="National ID or passport" />
          </div>
        </div>
      </div>

      <!-- Step 3: Materials Upload -->
      <div v-show="currentStep === 3" class="card">
        <div class="card-header">Step 3: Materials Upload</div>
        <div class="card-body">
          <div v-if="!registrationId" class="alert alert-warning">
            Please save the registration first (go back and proceed) before uploading materials.
          </div>

          <div v-else>
            <p class="text-muted mb-2">Upload required materials for each checklist item. Allowed: PDF, JPG, PNG (max 20MB each).</p>

            <div v-if="uploadInfo" class="mb-2">
              <small class="text-muted">
                Storage used: {{ formatBytes(uploadInfo.used_bytes) }} / {{ formatBytes(uploadInfo.limit_bytes) }}
              </small>
            </div>

            <div v-for="item in checklistItems" :key="item.id" class="material-item card mb-1" style="border: 1px solid var(--color-border);">
              <div class="card-body">
                <div class="flex justify-between items-center mb-1">
                  <strong>{{ item.label }}</strong>
                  <span v-if="item.is_required" class="badge badge-submitted">Required</span>
                  <span v-else class="badge badge-draft">Optional</span>
                </div>
                <p v-if="item.description" class="text-muted" style="font-size: 12px;">{{ item.description }}</p>

                <div v-if="getMaterial(item.id)">
                  <div v-for="ver in getMaterial(item.id).versions" :key="ver.id" style="font-size: 12px; margin-top: 4px;">
                    v{{ ver.version_number }}: {{ ver.original_filename }} ({{ ver.status }})
                  </div>
                </div>

                <div class="mt-1">
                  <input type="file" :ref="'file_' + item.id" accept=".pdf,.jpg,.jpeg,.png" @change="handleFileSelect(item, $event)" />
                  <button
                    class="btn btn-sm btn-primary mt-1"
                    :disabled="!selectedFiles[item.id] || uploading[item.id]"
                    @click="uploadFile(item)"
                  >
                    <span v-if="uploading[item.id]" class="spinner"></span>
                    <span v-else>Upload</span>
                  </button>
                </div>
                <div v-if="uploadErrors[item.id]" class="text-danger" style="font-size: 12px; margin-top: 4px;">
                  {{ uploadErrors[item.id] }}
                </div>
              </div>
            </div>

            <!-- Supplementary Submission (available only during the 72-hour window) -->
            <div v-if="uploadInfo && uploadInfo.supplementary_eligible" class="card mt-2" style="border: 2px solid var(--color-warning, #f59e0b);">
              <div class="card-header" style="background: rgba(245,158,11,0.1);">
                <strong>Supplementary Submission</strong>
              </div>
              <div class="card-body">
                <p class="text-muted mb-1">
                  You may submit additional or corrected materials once within 72 hours after the submission deadline.
                  This is a one-time action — once submitted it cannot be repeated.
                </p>
                <div v-if="suppError" class="alert alert-error mb-1">{{ suppError }}</div>
                <div v-if="suppSuccess" class="alert alert-success mb-1">{{ suppSuccess }}</div>

                <div class="form-group mb-2">
                  <label><strong>Correction Reason *</strong></label>
                  <textarea
                    v-model="suppCorrectionReason"
                    class="form-control"
                    rows="3"
                    placeholder="Explain why you are submitting corrected or additional materials..."
                    style="width: 100%;"
                  ></textarea>
                </div>

                <div v-for="item in checklistItems" :key="'supp_' + item.id" class="mb-1">
                  <label style="font-size: 13px;">{{ item.label }} correction file</label>
                  <input
                    type="file"
                    :ref="'supp_file_' + item.id"
                    accept=".pdf,.jpg,.jpeg,.png"
                    @change="handleSuppFileSelect(item, $event)"
                    style="display: block; margin-top: 4px;"
                  />
                  <div v-if="suppFileErrors[item.id]" class="text-danger" style="font-size: 12px;">{{ suppFileErrors[item.id] }}</div>
                </div>

                <button
                  class="btn btn-warning mt-1"
                  :disabled="suppSubmitting || !hasAnySuppFile || !suppCorrectionReason.trim()"
                  @click="submitSupplementary"
                >
                  <span v-if="suppSubmitting" class="spinner"></span>
                  <span v-else>Submit Supplementary Materials</span>
                </button>
              </div>
            </div>

            <p v-if="checklistItems.length === 0" class="text-muted">No checklist items defined for this batch.</p>
          </div>
        </div>
      </div>

      <!-- Step 4: Review & Submit -->
      <div v-show="currentStep === 4" class="card">
        <div class="card-header">Step 4: Review &amp; Submit</div>
        <div class="card-body">
          <div v-if="submitError" class="alert alert-error mb-2">{{ submitError }}</div>
          <div v-if="submitSuccess" class="alert alert-success mb-2">Registration submitted successfully!</div>

          <h3 class="mb-1">Summary</h3>
          <table>
            <tbody>
              <tr><td><strong>Title</strong></td><td>{{ form.title || '--' }}</td></tr>
              <tr><td><strong>Activity Type</strong></td><td>{{ form.activity_type || '--' }}</td></tr>
              <tr><td><strong>Applicant</strong></td><td>{{ form.applicant_name || '--' }}</td></tr>
              <tr><td><strong>Description</strong></td><td>{{ form.description || '--' }}</td></tr>
              <tr><td><strong>Start Date</strong></td><td>{{ form.start_date || '--' }}</td></tr>
              <tr><td><strong>End Date</strong></td><td>{{ form.end_date || '--' }}</td></tr>
              <tr><td><strong>Budget</strong></td><td>{{ form.requested_budget != null ? '$' + form.requested_budget : '--' }}</td></tr>
              <tr><td><strong>Email</strong></td><td>{{ form.applicant_email || '--' }}</td></tr>
              <tr><td><strong>Phone</strong></td><td>{{ form.applicant_phone || '--' }}</td></tr>
            </tbody>
          </table>

          <h3 class="mt-2 mb-1">Materials</h3>
          <div v-if="materials.length > 0">
            <div v-for="mat in materials" :key="mat.id" style="margin-bottom: 4px;">
              {{ checklistItems.find(i => i.id === mat.checklist_item_id)?.label || mat.checklist_item_id }}: {{ mat.versions?.length || 0 }} version(s)
            </div>
          </div>
          <p v-else class="text-muted">No materials uploaded yet.</p>

          <div class="mt-2">
            <button
              class="btn btn-success"
              :disabled="submitting || submitSuccess || registrationStatus !== 'draft'"
              @click="submitRegistration"
            >
              <span v-if="submitting" class="spinner"></span>
              <span v-else>Submit Registration</span>
            </button>
            <span v-if="registrationStatus !== 'draft'" class="ml-1 text-muted">
              Status: {{ registrationStatus }} (only drafts can be submitted)
            </span>
          </div>
        </div>
      </div>

      <!-- Navigation Buttons -->
      <div class="flex justify-between mt-2">
        <button class="btn btn-outline" :disabled="currentStep <= 1" @click="prevStep">
          Previous
        </button>
        <div class="flex gap-1">
          <button class="btn btn-outline" @click="saveDraft" :disabled="saving">
            <span v-if="saving" class="spinner"></span>
            <span v-else>Save Draft</span>
          </button>
          <button class="btn btn-primary" :disabled="currentStep >= 4" @click="nextStep">
            Next
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'RegistrationWizardPage',
  data() {
    return {
      loading: false,
      loadError: '',
      currentStep: 1,
      stepLabels: ['Basic Info', 'Details & Budget', 'Materials Upload', 'Review & Submit'],
      registrationId: null,
      registrationStatus: 'draft',
      batches: [],
      checklistItems: [],
      materials: [],
      uploadInfo: null,
      form: {
        batch_id: '',
        title: '',
        activity_type: '',
        applicant_name: '',
        description: '',
        start_date: '',
        end_date: '',
        requested_budget: null,
        applicant_email: '',
        applicant_phone: '',
        applicant_id_number: '',
      },
      saving: false,
      autosaveMsg: '',
      autosaveTimer: null,
      selectedFiles: {},
      uploading: {},
      uploadErrors: {},
      submitting: false,
      submitError: '',
      submitSuccess: false,
      suppFiles: {},
      suppFileErrors: {},
      suppSubmitting: false,
      suppError: '',
      suppSuccess: '',
      suppCorrectionReason: '',
    }
  },
  computed: {
    isEdit() {
      return !!this.$route.params.id
    },
    hasAnySuppFile() {
      return Object.values(this.suppFiles).some(f => f !== null && f !== undefined)
    },
  },
  methods: {
    goToStep(step) {
      if (step <= this.currentStep + 1) {
        this.currentStep = step
      }
    },
    prevStep() {
      if (this.currentStep > 1) this.currentStep--
    },
    async nextStep() {
      // Auto-save before moving forward
      if (this.registrationId && this.registrationStatus === 'draft') {
        await this.saveDraft()
      } else if (!this.registrationId && this.currentStep === 1) {
        // Create the registration first
        await this.createRegistration()
        if (!this.registrationId) return // creation failed
      }

      if (this.currentStep === 3 && this.registrationId) {
        await this.loadMaterials()
      }

      if (this.currentStep < 4) this.currentStep++
    },
    async createRegistration() {
      if (!this.form.batch_id) {
        this.loadError = 'Please select a collection batch.'
        return
      }
      this.saving = true
      this.loadError = ''
      try {
        const payload = { batch_id: this.form.batch_id }
        // Include any fields that have values
        if (this.form.title) payload.title = this.form.title
        if (this.form.activity_type) payload.activity_type = this.form.activity_type
        if (this.form.applicant_name) payload.applicant_name = this.form.applicant_name

        const res = await api.post('/registrations', payload)
        this.registrationId = res.data.id
        this.registrationStatus = res.data.status

        // Now update draft with remaining fields
        await this.saveDraft()
        await this.loadChecklistItems(res.data.batch_id)
      } catch (err) {
        this.loadError = err.response?.data?.detail || 'Failed to create registration.'
      } finally {
        this.saving = false
      }
    },
    async saveDraft() {
      if (!this.registrationId || this.registrationStatus !== 'draft') return
      this.saving = true
      this.autosaveMsg = ''
      try {
        const payload = {}
        const fields = ['title', 'activity_type', 'applicant_name', 'description',
          'requested_budget', 'applicant_email', 'applicant_phone', 'applicant_id_number']
        for (const f of fields) {
          if (this.form[f] !== '' && this.form[f] !== null && this.form[f] !== undefined) {
            payload[f] = this.form[f]
          }
        }

        // Handle dates
        if (this.form.start_date) {
          payload.start_date = new Date(this.form.start_date).toISOString()
        }
        if (this.form.end_date) {
          payload.end_date = new Date(this.form.end_date).toISOString()
        }

        // Include wizard_step
        payload.wizard_step = this.currentStep

        const res = await api.put(`/registrations/${this.registrationId}/draft`, payload)
        this.registrationStatus = res.data.status
        this.autosaveMsg = 'Draft saved at ' + new Date().toLocaleTimeString()
      } catch (err) {
        this.autosaveMsg = 'Autosave failed: ' + (err.response?.data?.detail || 'Unknown error')
      } finally {
        this.saving = false
      }
    },
    async loadRegistration() {
      this.loading = true
      try {
        const res = await api.get(`/registrations/${this.$route.params.id}`)
        const data = res.data
        this.registrationId = data.id
        this.registrationStatus = data.status
        this.form.batch_id = data.batch_id
        this.form.title = data.title || ''
        this.form.activity_type = data.activity_type || ''
        this.form.applicant_name = data.applicant_name || ''
        this.form.description = data.description || ''
        this.form.requested_budget = data.requested_budget
        this.form.applicant_email = data.applicant_email || ''
        this.form.applicant_phone = data.applicant_phone || ''
        this.form.applicant_id_number = data.applicant_id_number || ''
        this.currentStep = data.wizard_step || 1

        if (data.start_date) {
          this.form.start_date = data.start_date.substring(0, 16)
        }
        if (data.end_date) {
          this.form.end_date = data.end_date.substring(0, 16)
        }

        await this.loadChecklistItems(data.batch_id)
        await this.loadMaterials()
      } catch (err) {
        this.loadError = err.response?.data?.detail || 'Failed to load registration.'
      } finally {
        this.loading = false
      }
    },
    async loadBatches() {
      try {
        const res = await api.get('/batches')
        this.batches = res.data
      } catch {
        // silently fail
      }
    },
    async loadChecklistItems(batchId) {
      if (!batchId) return
      try {
        const res = await api.get(`/batches/${batchId}/checklist`)
        this.checklistItems = res.data
      } catch {
        // silently fail
      }
    },
    async loadMaterials() {
      if (!this.registrationId) return
      try {
        const [matRes, infoRes] = await Promise.all([
          api.get(`/registrations/${this.registrationId}/materials`),
          api.get(`/registrations/${this.registrationId}/materials/upload-info`),
        ])
        this.materials = matRes.data
        this.uploadInfo = infoRes.data
      } catch {
        // silently fail
      }
    },
    getMaterial(checklistItemId) {
      return this.materials.find((m) => m.checklist_item_id === checklistItemId)
    },
    handleFileSelect(item, event) {
      const file = event.target.files[0] || null
      this.uploadErrors[item.id] = ''
      this.selectedFiles = { ...this.selectedFiles, [item.id]: null }
      if (!file) return

      const allowedExts = ['.pdf', '.jpg', '.jpeg', '.png']
      const ext = '.' + file.name.split('.').pop().toLowerCase()
      if (!allowedExts.includes(ext) && !['application/pdf', 'image/jpeg', 'image/png'].includes(file.type)) {
        this.uploadErrors = { ...this.uploadErrors, [item.id]: 'File type not allowed. Use PDF, JPG, or PNG.' }
        event.target.value = ''
        return
      }

      const MAX_FILE = 20 * 1024 * 1024
      if (file.size > MAX_FILE) {
        this.uploadErrors = { ...this.uploadErrors, [item.id]: 'File exceeds maximum size of 20 MB.' }
        event.target.value = ''
        return
      }

      if (this.uploadInfo) {
        const pendingSizes = Object.values(this.selectedFiles).reduce((sum, f) => sum + (f ? f.size : 0), 0)
        const projected = this.uploadInfo.used_bytes + pendingSizes + file.size
        if (projected > this.uploadInfo.limit_bytes) {
          this.uploadErrors = { ...this.uploadErrors, [item.id]: 'Upload would exceed total 200 MB storage limit.' }
          event.target.value = ''
          return
        }
      }

      this.selectedFiles = { ...this.selectedFiles, [item.id]: file }
    },
    async uploadFile(item) {
      const file = this.selectedFiles[item.id]
      if (!file || !this.registrationId) return

      this.uploading[item.id] = true
      this.uploadErrors[item.id] = ''

      try {
        // Ensure material record exists
        let material = this.getMaterial(item.id)
        if (!material) {
          const matRes = await api.post(
            `/registrations/${this.registrationId}/materials?checklist_item_id=${item.id}`
          )
          material = { ...matRes.data, versions: [] }
          this.materials.push(material)
        }

        // Upload file version
        const formData = new FormData()
        formData.append('file', file)
        await api.post(
          `/registrations/${this.registrationId}/materials/${material.id}/versions`,
          formData,
          { headers: { 'Content-Type': 'multipart/form-data' } }
        )

        // Reload materials
        await this.loadMaterials()
        this.selectedFiles[item.id] = null
      } catch (err) {
        this.uploadErrors[item.id] = err.response?.data?.detail || 'Upload failed.'
      } finally {
        this.uploading[item.id] = false
      }
    },
    async submitRegistration() {
      this.submitting = true
      this.submitError = ''
      this.submitSuccess = false

      try {
        await api.post(`/registrations/${this.registrationId}/submit`)
        this.submitSuccess = true
        this.registrationStatus = 'submitted'
      } catch (err) {
        const detail = err.response?.data?.detail
        if (detail && detail.validation_errors) {
          this.submitError = detail.validation_errors.join('; ')
        } else {
          this.submitError = detail || 'Submission failed.'
        }
      } finally {
        this.submitting = false
      }
    },
    handleSuppFileSelect(item, event) {
      const file = event.target.files[0] || null
      this.$set ? this.$set(this.suppFileErrors, item.id, '') : (this.suppFileErrors[item.id] = '')
      this.suppFileErrors = { ...this.suppFileErrors, [item.id]: '' }
      if (!file) {
        this.suppFiles = { ...this.suppFiles, [item.id]: null }
        return
      }

      const allowedExts = ['.pdf', '.jpg', '.jpeg', '.png']
      const ext = '.' + file.name.split('.').pop().toLowerCase()
      if (!allowedExts.includes(ext) && !['application/pdf', 'image/jpeg', 'image/png'].includes(file.type)) {
        this.suppFileErrors = { ...this.suppFileErrors, [item.id]: 'File type not allowed. Use PDF, JPG, or PNG.' }
        event.target.value = ''
        return
      }

      if (file.size > 20 * 1024 * 1024) {
        this.suppFileErrors = { ...this.suppFileErrors, [item.id]: 'File exceeds 20 MB.' }
        event.target.value = ''
        return
      }

      // Real-time aggregate-size check. Supplementary uploads count
      // against the same 200 MB total as normal uploads (the backend
      // enforces the same 200 MB cap in ``materials.supplementary_submit``)
      // — the previous version of this handler only checked per-file
      // size, which meant the user did not see the total-budget error
      // until after clicking Submit. Mirrors the normal-upload check in
      // ``handleFileSelect`` above.
      if (this.uploadInfo) {
        // Replace any previously selected file for this checklist item
        // in the tally so the projection reflects the user's current
        // intent rather than double-counting.
        const otherSuppSizes = Object.entries(this.suppFiles)
          .filter(([ciId]) => ciId !== String(item.id))
          .reduce((sum, [, f]) => sum + (f ? f.size : 0), 0)
        const projected =
          this.uploadInfo.used_bytes + otherSuppSizes + file.size
        if (projected > this.uploadInfo.limit_bytes) {
          this.suppFileErrors = {
            ...this.suppFileErrors,
            [item.id]:
              'Supplementary upload would exceed the total 200 MB storage limit.',
          }
          event.target.value = ''
          return
        }
      }

      this.suppFiles = { ...this.suppFiles, [item.id]: file }
    },
    async submitSupplementary() {
      if (!this.registrationId || !this.hasAnySuppFile) return
      this.suppSubmitting = true
      this.suppError = ''
      this.suppSuccess = ''

      // Belt-and-suspenders: re-check the aggregate size at submit
      // time in case ``uploadInfo`` refreshed after the per-file
      // selection (e.g. another session added files). The backend
      // still enforces the cap authoritatively; this is the
      // real-time UX guard the Prompt requires.
      if (this.uploadInfo) {
        const total = Object.values(this.suppFiles).reduce(
          (sum, f) => sum + (f ? f.size : 0), 0,
        )
        if (this.uploadInfo.used_bytes + total > this.uploadInfo.limit_bytes) {
          this.suppError =
            'Supplementary upload would exceed the total 200 MB storage limit.'
          this.suppSubmitting = false
          return
        }
      }

      try {
        const formData = new FormData()
        formData.append('correction_reason', this.suppCorrectionReason.trim())
        const materialIds = []
        for (const [checklistItemId, file] of Object.entries(this.suppFiles)) {
          if (!file) continue
          let material = this.getMaterial(checklistItemId)
          if (!material) {
            const matRes = await api.post(
              `/registrations/${this.registrationId}/materials?checklist_item_id=${checklistItemId}`
            )
            material = { ...matRes.data, versions: [] }
            this.materials.push(material)
          }
          formData.append('files', file, file.name)
          materialIds.push(material.id)
        }

        const params = materialIds.map(id => `material_ids=${id}`).join('&')
        await api.post(
          `/registrations/${this.registrationId}/supplementary-submit?${params}`,
          formData,
          { headers: { 'Content-Type': 'multipart/form-data' } }
        )
        this.suppSuccess = 'Supplementary materials submitted successfully.'
        this.suppFiles = {}
        this.suppCorrectionReason = ''
        this.registrationStatus = 'supplemented'
        await this.loadMaterials()
      } catch (err) {
        this.suppError = err.response?.data?.detail || 'Supplementary submission failed.'
      } finally {
        this.suppSubmitting = false
      }
    },
    formatBytes(bytes) {
      if (bytes === 0) return '0 B'
      const k = 1024
      const sizes = ['B', 'KB', 'MB', 'GB']
      const i = Math.floor(Math.log(bytes) / Math.log(k))
      return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
    },
    startAutosave() {
      this.autosaveTimer = setInterval(() => {
        if (this.registrationId && this.registrationStatus === 'draft') {
          this.saveDraft()
        }
      }, 30000) // every 30 seconds
    },
  },
  async mounted() {
    await this.loadBatches()
    if (this.isEdit) {
      await this.loadRegistration()
    }
    this.startAutosave()
  },
  beforeUnmount() {
    if (this.autosaveTimer) {
      clearInterval(this.autosaveTimer)
    }
  },
}
</script>

<style scoped>
.material-item {
  margin-bottom: 12px;
}
</style>
