<template>
  <div class="app-layout">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="brand-icon">EP</div>
        <span class="brand-text">Eagle Point</span>
      </div>

      <nav class="sidebar-nav">
        <router-link to="/" class="nav-item" exact-active-class="active">
          <span class="nav-icon">&#9632;</span>
          Dashboard
        </router-link>

        <router-link
          v-if="canSee(['applicant', 'reviewer', 'financial_admin', 'system_admin'])"
          to="/registrations"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">&#9776;</span>
          Registrations
        </router-link>

        <router-link
          v-if="canSee(['applicant', 'system_admin'])"
          to="/registrations/new"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">&#43;</span>
          New Registration
        </router-link>

        <router-link
          v-if="canSee(['reviewer', 'system_admin'])"
          to="/reviews"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">&#10003;</span>
          Reviews
        </router-link>

        <router-link
          v-if="canSee(['financial_admin', 'system_admin'])"
          to="/finance"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">&#36;</span>
          Finance
        </router-link>

        <router-link
          v-if="canSee(['financial_admin', 'system_admin'])"
          to="/reports"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">&#128196;</span>
          Reports
        </router-link>

        <router-link
          v-if="canSee(['system_admin'])"
          to="/admin"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">&#9881;</span>
          Admin
        </router-link>
      </nav>

      <div class="sidebar-footer">
        <div class="user-info">
          <span class="user-role badge" :class="'badge-' + role">{{ role }}</span>
        </div>
        <button class="btn btn-outline btn-sm" @click="logout" style="width:100%">
          Logout
        </button>
      </div>
    </aside>

    <div class="main-area">
      <header class="top-bar">
        <div class="top-bar-left">
          <h2>{{ currentPageTitle }}</h2>
        </div>
        <div class="top-bar-right">
          <button class="notification-bell" @click="toggleNotifications" :title="'Notifications'">
            <span class="bell-icon">&#128276;</span>
            <span v-if="unreadCount > 0" class="bell-badge">{{ unreadCount }}</span>
          </button>
        </div>
      </header>

      <div v-if="showNotifications" class="notification-panel">
        <div class="notification-panel-header">
          <strong>Notifications</strong>
          <button class="btn btn-sm btn-outline" @click="showNotifications = false">Close</button>
        </div>
        <div v-if="notifications.length === 0" class="notification-empty">
          No notifications
        </div>
        <div
          v-for="notif in notifications"
          :key="notif.id"
          class="notification-item"
          :class="{ unread: !notif.read }"
          @click="markRead(notif)"
        >
          <span class="notification-severity" :class="'severity-' + notif.severity">
            {{ notif.severity }}
          </span>
          <span class="notification-msg">{{ notif.message }}</span>
        </div>
      </div>

      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'AppLayout',
  data() {
    return {
      role: localStorage.getItem('userRole') || '',
      showNotifications: false,
      notifications: [],
      unreadCount: 0,
    }
  },
  computed: {
    currentPageTitle() {
      return this.$route.name || 'Dashboard'
    },
  },
  methods: {
    canSee(roles) {
      return roles.includes(this.role)
    },
    logout() {
      localStorage.removeItem('token')
      localStorage.removeItem('userRole')
      localStorage.removeItem('userId')
      this.$router.push('/login')
    },
    async toggleNotifications() {
      this.showNotifications = !this.showNotifications
      if (this.showNotifications) {
        await this.fetchNotifications()
      }
    },
    async fetchNotifications() {
      try {
        const res = await api.get('/notifications', { params: { page_size: 20 } })
        this.notifications = res.data
        this.unreadCount = res.data.filter((n) => !n.read).length
      } catch {
        // silently fail
      }
    },
    async markRead(notif) {
      if (notif.read) return
      try {
        await api.put(`/notifications/${notif.id}/read`)
        notif.read = true
        this.unreadCount = Math.max(0, this.unreadCount - 1)
      } catch {
        // silently fail
      }
    },
    async fetchUnreadCount() {
      try {
        const res = await api.get('/notifications', { params: { unread: true, page_size: 1 } })
        // We get back the items; the count is the length but limited.
        // For a simple count, just get unread notifications
        const res2 = await api.get('/notifications', { params: { unread: true, page_size: 100 } })
        this.unreadCount = res2.data.length
      } catch {
        // silently fail
      }
    },
  },
  mounted() {
    this.fetchUnreadCount()
    // Poll every 60 seconds
    this._notifInterval = setInterval(() => this.fetchUnreadCount(), 60000)
  },
  beforeUnmount() {
    clearInterval(this._notifInterval)
  },
}
</script>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  width: var(--sidebar-width);
  background: var(--color-primary-dark);
  color: #fff;
  display: flex;
  flex-direction: column;
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 100;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.brand-icon {
  width: 32px;
  height: 32px;
  background: var(--color-primary-light);
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
}

.brand-text {
  font-weight: 600;
  font-size: 15px;
}

.sidebar-nav {
  flex: 1;
  padding: 12px 0;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  color: rgba(255, 255, 255, 0.75);
  font-size: 14px;
  font-weight: 500;
  text-decoration: none;
  transition: background 0.15s, color 0.15s;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  text-decoration: none;
}

.nav-item.active {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  border-right: 3px solid #fff;
}

.nav-icon {
  font-size: 16px;
  width: 20px;
  text-align: center;
}

.sidebar-footer {
  padding: 16px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.user-info {
  margin-bottom: 8px;
  text-align: center;
}

.main-area {
  flex: 1;
  margin-left: var(--sidebar-width);
  display: flex;
  flex-direction: column;
}

.top-bar {
  height: var(--header-height);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  position: sticky;
  top: 0;
  z-index: 50;
}

.top-bar-left h2 {
  font-size: 16px;
  font-weight: 600;
}

.top-bar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.notification-bell {
  position: relative;
  background: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  padding: 6px 10px;
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
}

.notification-bell:hover {
  background: var(--color-bg);
}

.bell-badge {
  position: absolute;
  top: -6px;
  right: -6px;
  background: var(--color-danger);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.notification-panel {
  position: fixed;
  top: var(--header-height);
  right: 0;
  width: 360px;
  max-height: calc(100vh - var(--header-height));
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  box-shadow: var(--shadow-md);
  overflow-y: auto;
  z-index: 200;
}

.notification-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border);
}

.notification-empty {
  padding: 24px 16px;
  text-align: center;
  color: var(--color-text-muted);
}

.notification-item {
  padding: 10px 16px;
  border-bottom: 1px solid var(--color-border);
  cursor: pointer;
  transition: background 0.15s;
}

.notification-item:hover {
  background: var(--color-bg);
}

.notification-item.unread {
  background: #ebf8ff;
}

.notification-severity {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  margin-right: 6px;
  padding: 1px 6px;
  border-radius: 3px;
}

.severity-warning {
  background: #fefcbf;
  color: #744210;
}

.severity-critical {
  background: #fed7d7;
  color: #9b2c2c;
}

.severity-info {
  background: #bee3f8;
  color: #2b6cb0;
}

.notification-msg {
  font-size: 13px;
}

.content {
  flex: 1;
  padding: 24px;
}
</style>
