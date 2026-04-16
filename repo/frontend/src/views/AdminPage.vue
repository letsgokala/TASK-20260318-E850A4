<template>
  <div>
    <div class="page-header">
      <h1>Administration</h1>
      <p>User management, backups, and system configuration.</p>
    </div>

    <!-- User Management -->
    <div class="card mb-2">
      <div class="card-header flex justify-between items-center">
        <span>User Management</span>
        <button class="btn btn-sm btn-primary" @click="showCreateUser = true">+ Create User</button>
      </div>
      <div class="card-body">
        <div v-if="loadingUsers" class="text-center"><span class="spinner"></span></div>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Username</th>
                <th>Role</th>
                <th>Active</th>
                <th>Locked</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="user in users" :key="user.id">
                <td>{{ user.username }}</td>
                <td><span class="badge" :class="'badge-' + statusForRole(user.role)">{{ user.role }}</span></td>
                <td>
                  <span :class="user.is_active ? 'text-success' : 'text-danger'">
                    {{ user.is_active ? 'Yes' : 'No' }}
                  </span>
                </td>
                <td>
                  <span v-if="user.locked_until" class="text-danger">
                    Until {{ formatDate(user.locked_until) }}
                  </span>
                  <span v-else class="text-muted">--</span>
                </td>
                <td class="text-muted">{{ formatDate(user.created_at) }}</td>
                <td>
                  <div class="flex gap-1">
                    <button
                      v-if="user.is_active"
                      class="btn btn-sm btn-danger"
                      @click="deactivateUser(user)"
                      :disabled="user.id === currentUserId"
                      :title="user.id === currentUserId ? 'Cannot deactivate yourself' : 'Deactivate'"
                    >
                      Deactivate
                    </button>
                    <button
                      v-if="user.locked_until"
                      class="btn btn-sm btn-warning"
                      @click="unlockUser(user)"
                    >
                      Unlock
                    </button>
                    <button
                      class="btn btn-sm btn-outline"
                      @click="openResetPassword(user)"
                    >
                      Reset Password
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Backups -->
    <div class="card mb-2">
      <div class="card-header">Backups</div>
      <div class="card-body">
        <div v-if="loadingBackups" class="text-center"><span class="spinner"></span></div>
        <div v-else-if="backups.length > 0" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>DB Dump</th>
                <th>Size</th>
                <th>File Backup</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="backup in backups" :key="backup.date">
                <td>{{ backup.date }}</td>
                <td>{{ backup.db_dump }}</td>
                <td>{{ formatBytes(backup.db_size_bytes) }}</td>
                <td>
                  <span :class="backup.has_file_backup ? 'text-success' : 'text-muted'">
                    {{ backup.has_file_backup ? 'Yes' : 'No' }}
                  </span>
                </td>
                <td>
                  <button class="btn btn-sm btn-warning" @click="confirmRestore(backup)" :disabled="restoring">
                    Restore
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="text-muted">No backups found.</p>

        <div v-if="restoreResult" class="alert mt-1" :class="restoreResult.status === 'complete' ? 'alert-success' : 'alert-warning'">
          {{ restoreResult.detail }}
        </div>
      </div>
    </div>

    <!-- Batches & Checklists -->
    <div class="card mb-2">
      <div class="card-header flex justify-between items-center">
        <span>Collection Batches &amp; Checklists</span>
        <button class="btn btn-sm btn-primary" @click="openCreateBatch">+ Create Batch</button>
      </div>
      <div class="card-body">
        <div v-if="loadingBatches" class="text-center"><span class="spinner"></span></div>
        <div v-else-if="batches.length > 0" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Submission Deadline</th>
                <th>Supplementary Deadline</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="b in batches" :key="b.id">
                <tr>
                  <td>
                    <strong>{{ b.name }}</strong>
                    <div v-if="b.description" class="text-muted" style="font-size: 12px;">{{ b.description }}</div>
                  </td>
                  <td class="text-muted">{{ formatDate(b.submission_deadline) }}</td>
                  <td class="text-muted">{{ formatDate(b.supplementary_deadline) }}</td>
                  <td>
                    <div class="flex gap-1">
                      <button class="btn btn-sm btn-outline" @click="toggleChecklist(b)">
                        {{ expandedBatchId === b.id ? 'Hide Checklist' : 'Manage Checklist' }}
                      </button>
                      <button class="btn btn-sm btn-outline" @click="openEditBatch(b)">Edit</button>
                    </div>
                  </td>
                </tr>
                <tr v-if="expandedBatchId === b.id">
                  <td colspan="4" style="background: var(--color-bg-subtle, #f8f8f8);">
                    <div class="p-1">
                      <div class="flex justify-between items-center mb-1">
                        <strong>Checklist items</strong>
                        <button class="btn btn-sm btn-primary" @click="openCreateChecklist(b)">+ Add Item</button>
                      </div>
                      <div v-if="checklistLoading[b.id]" class="text-center"><span class="spinner"></span></div>
                      <div v-else-if="(checklists[b.id] || []).length === 0" class="text-muted">
                        No checklist items defined. Add at least one required item so applicants can upload evidence.
                      </div>
                      <table v-else class="mt-1">
                        <thead>
                          <tr>
                            <th>Order</th>
                            <th>Label</th>
                            <th>Description</th>
                            <th>Required</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="ci in checklists[b.id]" :key="ci.id">
                            <td>{{ ci.sort_order }}</td>
                            <td>{{ ci.label }}</td>
                            <td class="text-muted">{{ ci.description || '--' }}</td>
                            <td>
                              <span :class="ci.is_required ? 'text-success' : 'text-muted'">
                                {{ ci.is_required ? 'Required' : 'Optional' }}
                              </span>
                            </td>
                          </tr>
                        </tbody>
                      </table>
                      <div v-if="checklistError[b.id]" class="alert alert-error mt-1">
                        {{ checklistError[b.id] }}
                      </div>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <p v-else class="text-muted">No batches defined. Create one so applicants can submit registrations.</p>
        <div v-if="batchListError" class="alert alert-error mt-1">{{ batchListError }}</div>
      </div>
    </div>

    <!-- Create / Edit Batch Modal -->
    <div v-if="showBatchModal" class="modal-overlay" @click.self="showBatchModal = false">
      <div class="modal">
        <h3>{{ editingBatchId ? 'Edit Batch' : 'Create Batch' }}</h3>
        <div v-if="batchModalError" class="alert alert-error">{{ batchModalError }}</div>
        <div class="form-group">
          <label>Name</label>
          <input v-model="batchForm.name" type="text" class="form-control" />
        </div>
        <div class="form-group">
          <label>Description</label>
          <textarea v-model="batchForm.description" class="form-control" rows="2"></textarea>
        </div>
        <div class="form-group">
          <label>Submission Deadline</label>
          <input v-model="batchForm.submission_deadline" type="datetime-local" class="form-control" />
        </div>
        <div class="modal-actions">
          <button class="btn btn-outline" @click="showBatchModal = false">Cancel</button>
          <button class="btn btn-primary" :disabled="batchModalSubmitting" @click="submitBatchModal">
            <span v-if="batchModalSubmitting" class="spinner"></span>
            <span v-else>{{ editingBatchId ? 'Save' : 'Create' }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Create Checklist Item Modal -->
    <div v-if="showChecklistModal" class="modal-overlay" @click.self="showChecklistModal = false">
      <div class="modal">
        <h3>Add Checklist Item</h3>
        <div v-if="checklistModalError" class="alert alert-error">{{ checklistModalError }}</div>
        <div class="form-group">
          <label>Label</label>
          <input v-model="checklistForm.label" type="text" class="form-control" placeholder="e.g. Research Proposal" />
        </div>
        <div class="form-group">
          <label>Description (optional)</label>
          <textarea v-model="checklistForm.description" class="form-control" rows="2"></textarea>
        </div>
        <div class="form-group">
          <label>Sort Order</label>
          <input v-model.number="checklistForm.sort_order" type="number" step="1" class="form-control" />
        </div>
        <div class="form-group">
          <label style="display: flex; align-items: center; gap: 8px;">
            <input v-model="checklistForm.is_required" type="checkbox" />
            Required for submission
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn btn-outline" @click="showChecklistModal = false">Cancel</button>
          <button class="btn btn-primary" :disabled="checklistModalSubmitting" @click="submitChecklistModal">
            <span v-if="checklistModalSubmitting" class="spinner"></span>
            <span v-else>Add</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Audit Log Viewer -->
    <div class="card mb-2">
      <div class="card-header flex justify-between items-center">
        <span>Audit Log</span>
        <button class="btn btn-sm btn-outline" @click="loadAuditLogs" :disabled="loadingAuditLogs">
          Refresh
        </button>
      </div>
      <div class="card-body">
        <div class="flex gap-1 mb-1" style="flex-wrap: wrap;">
          <input
            v-model="auditFilters.action"
            type="text"
            class="form-control"
            placeholder="Action contains..."
            style="max-width: 220px;"
            @keyup.enter="loadAuditLogs"
          />
          <input
            v-model="auditFilters.resource_type"
            type="text"
            class="form-control"
            placeholder="Resource type"
            style="max-width: 180px;"
            @keyup.enter="loadAuditLogs"
          />
          <input
            v-model="auditFilters.user_id"
            type="text"
            class="form-control"
            placeholder="User ID (UUID)"
            style="max-width: 260px;"
            @keyup.enter="loadAuditLogs"
          />
          <input
            v-model="auditFilters.from_date"
            type="datetime-local"
            class="form-control"
            style="max-width: 200px;"
          />
          <input
            v-model="auditFilters.to_date"
            type="datetime-local"
            class="form-control"
            style="max-width: 200px;"
          />
          <button class="btn btn-sm btn-primary" @click="loadAuditLogs" :disabled="loadingAuditLogs">
            <span v-if="loadingAuditLogs" class="spinner"></span>
            <span v-else>Apply</span>
          </button>
          <button class="btn btn-sm btn-outline" @click="resetAuditFilters">Reset</button>
        </div>
        <div v-if="loadingAuditLogs" class="text-center"><span class="spinner"></span></div>
        <div v-else-if="auditLogs.length > 0" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>User</th>
                <th>Action</th>
                <th>Resource</th>
                <th>IP</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="log in auditLogs" :key="log.id">
                <td class="text-muted">{{ formatDate(log.created_at) }}</td>
                <td>{{ log.user_id ? log.user_id.slice(0, 8) : '—' }}</td>
                <td style="font-family: monospace; font-size: 12px;">{{ log.action }}</td>
                <td>
                  <span v-if="log.resource_type">{{ log.resource_type }}</span>
                  <span v-if="log.resource_id" class="text-muted"> ({{ log.resource_id.slice(0, 8) }})</span>
                </td>
                <td class="text-muted">{{ log.ip_address || '—' }}</td>
                <td class="text-muted" style="font-family: monospace; font-size: 11px;">
                  <pre style="margin: 0; white-space: pre-wrap;">{{ log.details ? JSON.stringify(log.details) : '—' }}</pre>
                </td>
              </tr>
            </tbody>
          </table>
          <div class="flex justify-between items-center mt-1">
            <span class="text-muted">Showing {{ auditLogs.length }} of {{ auditTotal }} entries</span>
            <div class="flex gap-1">
              <button
                class="btn btn-sm btn-outline"
                :disabled="auditPage <= 1 || loadingAuditLogs"
                @click="auditPage--; loadAuditLogs()"
              >
                Previous
              </button>
              <span class="text-muted">Page {{ auditPage }}</span>
              <button
                class="btn btn-sm btn-outline"
                :disabled="auditLogs.length < auditPageSize || loadingAuditLogs"
                @click="auditPage++; loadAuditLogs()"
              >
                Next
              </button>
            </div>
          </div>
        </div>
        <p v-else class="text-muted">No audit log entries matched the filters.</p>
      </div>
    </div>

    <!-- Integrity Check -->
    <div class="card mb-2">
      <div class="card-header flex justify-between items-center">
        <span>File Integrity Check</span>
        <button
          class="btn btn-sm btn-primary"
          :disabled="integrityRunning"
          @click="runIntegrityCheck"
        >
          <span v-if="integrityRunning" class="spinner"></span>
          <span v-else>Run Integrity Check</span>
        </button>
      </div>
      <div class="card-body">
        <p class="text-muted">
          Re-hashes every stored material file and verifies the SHA-256 against the
          database. Any missing or mismatched files are flagged below.
        </p>
        <div v-if="integrityResult" class="mt-1">
          <div
            class="alert"
            :class="(integrityResult.missing_count === 0 && integrityResult.mismatch_count === 0)
              ? 'alert-success' : 'alert-warning'"
          >
            {{ integrityResult.total }} files checked —
            {{ integrityResult.ok }} ok,
            {{ integrityResult.missing_count }} missing,
            {{ integrityResult.mismatch_count }} hash mismatch.
          </div>
          <div v-if="integrityResult.missing_count > 0" class="mt-1">
            <h4>Missing files</h4>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr><th>Version ID</th><th>Material ID</th><th>Expected path</th></tr>
                </thead>
                <tbody>
                  <tr v-for="m in integrityResult.missing" :key="m.version_id">
                    <td style="font-family: monospace; font-size: 12px;">{{ m.version_id }}</td>
                    <td style="font-family: monospace; font-size: 12px;">{{ m.material_id }}</td>
                    <td style="font-family: monospace; font-size: 12px;">{{ m.storage_path }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-if="integrityResult.mismatch_count > 0" class="mt-1">
            <h4>Hash mismatches</h4>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Version ID</th>
                    <th>Expected SHA-256</th>
                    <th>Actual SHA-256</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="m in integrityResult.hash_mismatch" :key="m.version_id">
                    <td style="font-family: monospace; font-size: 12px;">{{ m.version_id }}</td>
                    <td style="font-family: monospace; font-size: 11px;">{{ m.expected_hash }}</td>
                    <td style="font-family: monospace; font-size: 11px;">{{ m.actual_hash }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div v-if="integrityError" class="alert alert-error mt-1">{{ integrityError }}</div>
      </div>
    </div>

    <!-- Alert Threshold Configuration -->
    <div class="card mb-2">
      <div class="card-header">Alert Thresholds</div>
      <div class="card-body">
        <div v-if="loadingThresholds" class="text-center"><span class="spinner"></span></div>
        <div v-else-if="thresholds.length > 0">
          <div v-for="th in thresholds" :key="th.id" class="flex items-center gap-1 mb-1" style="padding: 8px 0; border-bottom: 1px solid var(--color-border);">
            <strong style="min-width: 180px;">{{ th.metric_name }}</strong>
            <select v-model="th._comparison" class="form-control" style="width: auto; min-width: 60px;">
              <option value="gt">&gt;</option>
              <option value="lt">&lt;</option>
            </select>
            <input v-model.number="th._value" type="number" step="0.01" class="form-control" style="width: 100px;" />
            <span class="text-muted">%</span>
            <button class="btn btn-sm btn-primary" @click="updateThreshold(th)" :disabled="th._saving">
              Save
            </button>
            <span v-if="th._saved" class="text-success" style="font-size: 12px;">Saved</span>
          </div>
        </div>
        <p v-else class="text-muted">No alert thresholds configured.</p>
      </div>
    </div>

    <!-- Create User Modal -->
    <div v-if="showCreateUser" class="modal-overlay" @click.self="showCreateUser = false">
      <div class="modal">
        <h3>Create User</h3>
        <div v-if="createUserError" class="alert alert-error">{{ createUserError }}</div>
        <div class="form-group">
          <label>Username</label>
          <input v-model="newUser.username" type="text" class="form-control" placeholder="Username" />
        </div>
        <div class="form-group">
          <label>Password</label>
          <input v-model="newUser.password" type="password" class="form-control" placeholder="Password" />
        </div>
        <div class="form-group">
          <label>Role</label>
          <select v-model="newUser.role" class="form-control">
            <option value="applicant">Applicant</option>
            <option value="reviewer">Reviewer</option>
            <option value="financial_admin">Financial Admin</option>
            <option value="system_admin">System Admin</option>
          </select>
        </div>
        <div class="modal-actions">
          <button class="btn btn-outline" @click="showCreateUser = false">Cancel</button>
          <button class="btn btn-primary" :disabled="creatingUser" @click="createUser">
            <span v-if="creatingUser" class="spinner"></span>
            <span v-else>Create</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Reset Password Modal -->
    <div v-if="showResetPassword" class="modal-overlay" @click.self="showResetPassword = false">
      <div class="modal">
        <h3>Reset Password for {{ resetPasswordUser?.username }}</h3>
        <div v-if="resetPasswordError" class="alert alert-error">{{ resetPasswordError }}</div>
        <div class="form-group">
          <label>New Password</label>
          <input v-model="newPassword" type="password" class="form-control" placeholder="Enter new password" />
        </div>
        <div class="modal-actions">
          <button class="btn btn-outline" @click="showResetPassword = false">Cancel</button>
          <button class="btn btn-primary" :disabled="resettingPassword" @click="resetPassword">
            <span v-if="resettingPassword" class="spinner"></span>
            <span v-else>Reset</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Restore Confirmation Modal -->
    <div v-if="showRestoreConfirm" class="modal-overlay">
      <div class="modal">
        <h3 style="color: var(--color-danger);">Confirm Restore</h3>
        <p>Are you sure you want to restore from backup <strong>{{ restoreDate }}</strong>?</p>
        <p class="text-muted mt-1">This will put the system in maintenance mode and replace the current database and files.</p>
        <div class="modal-actions">
          <button class="btn btn-outline" @click="showRestoreConfirm = false">Cancel</button>
          <button class="btn btn-danger" :disabled="restoring" @click="performRestore">
            <span v-if="restoring" class="spinner"></span>
            <span v-else>Confirm Restore</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'AdminPage',
  data() {
    return {
      currentUserId: localStorage.getItem('userId') || '',
      // Users
      users: [],
      loadingUsers: false,
      showCreateUser: false,
      newUser: { username: '', password: '', role: 'applicant' },
      creatingUser: false,
      createUserError: '',
      // Reset password
      showResetPassword: false,
      resetPasswordUser: null,
      newPassword: '',
      resettingPassword: false,
      resetPasswordError: '',
      // Backups
      backups: [],
      loadingBackups: false,
      showRestoreConfirm: false,
      restoreDate: '',
      restoring: false,
      restoreResult: null,
      // Thresholds
      thresholds: [],
      loadingThresholds: false,
      // Audit logs
      auditLogs: [],
      loadingAuditLogs: false,
      auditTotal: 0,
      auditPage: 1,
      auditPageSize: 50,
      auditFilters: {
        action: '',
        resource_type: '',
        user_id: '',
        from_date: '',
        to_date: '',
      },
      // Integrity check
      integrityResult: null,
      integrityError: '',
      integrityRunning: false,
      // Batches & checklists
      batches: [],
      loadingBatches: false,
      batchListError: '',
      expandedBatchId: null,
      checklists: {},
      checklistLoading: {},
      checklistError: {},
      showBatchModal: false,
      batchModalError: '',
      batchModalSubmitting: false,
      editingBatchId: null,
      batchForm: { name: '', description: '', submission_deadline: '' },
      showChecklistModal: false,
      checklistModalError: '',
      checklistModalSubmitting: false,
      checklistTargetBatchId: null,
      checklistForm: { label: '', description: '', sort_order: 1, is_required: true },
    }
  },
  methods: {
    formatDate(dateStr) {
      if (!dateStr) return '--'
      return new Date(dateStr).toLocaleString()
    },
    formatBytes(bytes) {
      if (!bytes) return '0 B'
      const k = 1024
      const sizes = ['B', 'KB', 'MB', 'GB']
      const i = Math.floor(Math.log(bytes) / Math.log(k))
      return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
    },
    statusForRole(role) {
      const map = {
        applicant: 'draft',
        reviewer: 'submitted',
        financial_admin: 'supplemented',
        system_admin: 'approved',
      }
      return map[role] || 'draft'
    },
    // Users
    async loadUsers() {
      this.loadingUsers = true
      try {
        const res = await api.get('/admin/users')
        this.users = res.data
      } catch {
        this.users = []
      } finally {
        this.loadingUsers = false
      }
    },
    async createUser() {
      this.creatingUser = true
      this.createUserError = ''
      try {
        await api.post('/admin/users', this.newUser)
        this.showCreateUser = false
        this.newUser = { username: '', password: '', role: 'applicant' }
        await this.loadUsers()
      } catch (err) {
        this.createUserError = err.response?.data?.detail || 'Failed to create user.'
      } finally {
        this.creatingUser = false
      }
    },
    async deactivateUser(user) {
      if (!confirm(`Deactivate user "${user.username}"?`)) return
      try {
        await api.put(`/admin/users/${user.id}/deactivate`)
        await this.loadUsers()
      } catch (err) {
        alert(err.response?.data?.detail || 'Failed to deactivate user.')
      }
    },
    async unlockUser(user) {
      try {
        await api.put(`/admin/users/${user.id}/unlock`)
        await this.loadUsers()
      } catch (err) {
        alert(err.response?.data?.detail || 'Failed to unlock user.')
      }
    },
    openResetPassword(user) {
      this.resetPasswordUser = user
      this.newPassword = ''
      this.resetPasswordError = ''
      this.showResetPassword = true
    },
    async resetPassword() {
      if (!this.newPassword) {
        this.resetPasswordError = 'Password is required.'
        return
      }
      this.resettingPassword = true
      this.resetPasswordError = ''
      try {
        await api.put(`/admin/users/${this.resetPasswordUser.id}/reset-password`, {
          new_password: this.newPassword,
        })
        this.showResetPassword = false
      } catch (err) {
        this.resetPasswordError = err.response?.data?.detail || 'Failed to reset password.'
      } finally {
        this.resettingPassword = false
      }
    },
    // Backups
    async loadBackups() {
      this.loadingBackups = true
      try {
        const res = await api.get('/admin/backups')
        this.backups = res.data
      } catch {
        this.backups = []
      } finally {
        this.loadingBackups = false
      }
    },
    confirmRestore(backup) {
      this.restoreDate = backup.date
      this.showRestoreConfirm = true
      this.restoreResult = null
    },
    async performRestore() {
      this.restoring = true
      try {
        const res = await api.post(`/admin/backups/${this.restoreDate}/restore`)
        this.restoreResult = res.data
        this.showRestoreConfirm = false
      } catch (err) {
        this.restoreResult = { status: 'error', detail: err.response?.data?.detail || 'Restore failed.' }
        this.showRestoreConfirm = false
      } finally {
        this.restoring = false
      }
    },
    // Thresholds
    async loadThresholds() {
      this.loadingThresholds = true
      try {
        const res = await api.get('/alert-thresholds')
        this.thresholds = res.data.map((th) => ({
          ...th,
          _comparison: th.comparison || 'gt',
          _value: parseFloat(th.threshold_value) || 0,
          _saving: false,
          _saved: false,
        }))
      } catch {
        this.thresholds = []
      } finally {
        this.loadingThresholds = false
      }
    },
    async updateThreshold(th) {
      th._saving = true
      th._saved = false
      try {
        await api.put(`/alert-thresholds/${th.id}`, {
          threshold_value: th._value,
          comparison: th._comparison,
        })
        th._saved = true
        setTimeout(() => { th._saved = false }, 3000)
      } catch (err) {
        alert(err.response?.data?.detail || 'Failed to update threshold.')
      } finally {
        th._saving = false
      }
    },
    // Audit log viewer
    resetAuditFilters() {
      this.auditFilters = {
        action: '',
        resource_type: '',
        user_id: '',
        from_date: '',
        to_date: '',
      }
      this.auditPage = 1
      this.loadAuditLogs()
    },
    async loadAuditLogs() {
      this.loadingAuditLogs = true
      try {
        const params = {
          page: this.auditPage,
          page_size: this.auditPageSize,
        }
        if (this.auditFilters.action) params.action = this.auditFilters.action
        if (this.auditFilters.resource_type) {
          params.resource_type = this.auditFilters.resource_type
        }
        if (this.auditFilters.user_id) params.user_id = this.auditFilters.user_id
        if (this.auditFilters.from_date) params.from = this.auditFilters.from_date
        if (this.auditFilters.to_date) params.to = this.auditFilters.to_date
        const res = await api.get('/admin/audit-logs', { params })
        this.auditLogs = res.data.items || []
        this.auditTotal = res.data.total || 0
      } catch (err) {
        this.auditLogs = []
        this.auditTotal = 0
        if (err.response?.status && err.response.status !== 200) {
          alert(err.response?.data?.detail || 'Failed to load audit logs.')
        }
      } finally {
        this.loadingAuditLogs = false
      }
    },
    // Batches & checklists
    async loadBatches() {
      this.loadingBatches = true
      this.batchListError = ''
      try {
        const res = await api.get('/batches')
        this.batches = res.data
      } catch (err) {
        this.batches = []
        this.batchListError = err.response?.data?.detail || 'Failed to load batches.'
      } finally {
        this.loadingBatches = false
      }
    },
    openCreateBatch() {
      this.editingBatchId = null
      this.batchForm = { name: '', description: '', submission_deadline: '' }
      this.batchModalError = ''
      this.showBatchModal = true
    },
    openEditBatch(b) {
      this.editingBatchId = b.id
      // datetime-local wants YYYY-MM-DDTHH:mm
      const iso = b.submission_deadline
        ? new Date(b.submission_deadline).toISOString().slice(0, 16)
        : ''
      this.batchForm = {
        name: b.name,
        description: b.description || '',
        submission_deadline: iso,
      }
      this.batchModalError = ''
      this.showBatchModal = true
    },
    async submitBatchModal() {
      if (!this.batchForm.name || !this.batchForm.submission_deadline) {
        this.batchModalError = 'Name and submission deadline are required.'
        return
      }
      this.batchModalSubmitting = true
      this.batchModalError = ''
      try {
        const payload = {
          name: this.batchForm.name,
          description: this.batchForm.description || null,
          submission_deadline: new Date(this.batchForm.submission_deadline).toISOString(),
        }
        if (this.editingBatchId) {
          await api.put(`/batches/${this.editingBatchId}`, payload)
        } else {
          await api.post('/batches', payload)
        }
        this.showBatchModal = false
        await this.loadBatches()
      } catch (err) {
        this.batchModalError = err.response?.data?.detail || 'Failed to save batch.'
      } finally {
        this.batchModalSubmitting = false
      }
    },
    async toggleChecklist(b) {
      if (this.expandedBatchId === b.id) {
        this.expandedBatchId = null
        return
      }
      this.expandedBatchId = b.id
      await this.loadChecklist(b.id)
    },
    async loadChecklist(batchId) {
      this.checklistLoading = { ...this.checklistLoading, [batchId]: true }
      this.checklistError = { ...this.checklistError, [batchId]: '' }
      try {
        const res = await api.get(`/batches/${batchId}/checklist`)
        this.checklists = { ...this.checklists, [batchId]: res.data }
      } catch (err) {
        this.checklists = { ...this.checklists, [batchId]: [] }
        this.checklistError = {
          ...this.checklistError,
          [batchId]: err.response?.data?.detail || 'Failed to load checklist items.',
        }
      } finally {
        this.checklistLoading = { ...this.checklistLoading, [batchId]: false }
      }
    },
    openCreateChecklist(b) {
      this.checklistTargetBatchId = b.id
      const existing = this.checklists[b.id] || []
      const nextOrder = existing.length > 0
        ? Math.max(...existing.map(c => c.sort_order || 0)) + 1
        : 1
      this.checklistForm = {
        label: '',
        description: '',
        sort_order: nextOrder,
        is_required: true,
      }
      this.checklistModalError = ''
      this.showChecklistModal = true
    },
    async submitChecklistModal() {
      if (!this.checklistForm.label) {
        this.checklistModalError = 'Label is required.'
        return
      }
      this.checklistModalSubmitting = true
      this.checklistModalError = ''
      try {
        await api.post(
          `/batches/${this.checklistTargetBatchId}/checklist`,
          this.checklistForm,
        )
        this.showChecklistModal = false
        await this.loadChecklist(this.checklistTargetBatchId)
      } catch (err) {
        this.checklistModalError = err.response?.data?.detail || 'Failed to add checklist item.'
      } finally {
        this.checklistModalSubmitting = false
      }
    },
    // Integrity check
    async runIntegrityCheck() {
      if (!confirm('Run integrity check across all material files? This may take a few minutes.')) {
        return
      }
      this.integrityRunning = true
      this.integrityError = ''
      this.integrityResult = null
      try {
        const res = await api.post('/admin/integrity-check')
        this.integrityResult = res.data
      } catch (err) {
        this.integrityError = err.response?.data?.detail || 'Integrity check failed.'
      } finally {
        this.integrityRunning = false
      }
    },
  },
  mounted() {
    this.loadUsers()
    this.loadBackups()
    this.loadThresholds()
    this.loadAuditLogs()
    this.loadBatches()
  },
}
</script>
