<template>
  <div class="login-wrapper">
    <div class="login-card">
      <div class="login-header">
        <div class="login-brand">EP</div>
        <h1>Eagle Point</h1>
        <p>Activity Registration &amp; Funding Audit Platform</p>
      </div>

      <form @submit.prevent="handleLogin">
        <div v-if="error" class="alert alert-error">{{ error }}</div>

        <div class="form-group">
          <label for="username">Username</label>
          <input
            id="username"
            v-model="username"
            type="text"
            class="form-control"
            placeholder="Enter your username"
            required
            autocomplete="username"
          />
        </div>

        <div class="form-group">
          <label for="password">Password</label>
          <input
            id="password"
            v-model="password"
            type="password"
            class="form-control"
            placeholder="Enter your password"
            required
            autocomplete="current-password"
          />
        </div>

        <button type="submit" class="btn btn-primary login-btn" :disabled="loading">
          <span v-if="loading" class="spinner"></span>
          <span v-else>Sign In</span>
        </button>
      </form>
    </div>
  </div>
</template>

<script>
import api from '../api.js'

export default {
  name: 'LoginPage',
  data() {
    return {
      username: '',
      password: '',
      error: '',
      loading: false,
    }
  },
  methods: {
    async handleLogin() {
      this.error = ''
      this.loading = true

      try {
        const res = await api.post('/auth/login', {
          username: this.username,
          password: this.password,
        })

        const token = res.data.access_token
        localStorage.setItem('token', token)

        // Decode the JWT payload (second segment). JWT uses base64url
        // encoding, which replaces + with - and / with _ and omits
        // padding. Plain atob() breaks on these characters, so we
        // normalise to standard base64 before decoding.
        const raw = token.split('.')[1]
        const base64 = raw.replace(/-/g, '+').replace(/_/g, '/')
        const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
        const payload = JSON.parse(atob(padded))
        localStorage.setItem('userRole', payload.role)
        localStorage.setItem('userId', payload.sub)

        this.$router.push('/')
      } catch (err) {
        if (err.response) {
          const status = err.response.status
          if (status === 401) {
            this.error = 'Invalid username or password.'
          } else if (status === 423) {
            this.error = 'Account is locked due to too many failed attempts. Please try again later.'
          } else {
            this.error = err.response.data?.detail || 'Login failed. Please try again.'
          }
        } else {
          this.error = 'Network error. Please check your connection.'
        }
      } finally {
        this.loading = false
      }
    },
  },
}
</script>

<style scoped>
.login-wrapper {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--color-primary-dark) 0%, var(--color-primary) 100%);
}

.login-card {
  background: var(--color-surface);
  border-radius: 8px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
  padding: 40px;
  width: 100%;
  max-width: 400px;
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.login-brand {
  width: 48px;
  height: 48px;
  background: var(--color-primary);
  color: #fff;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 20px;
  margin-bottom: 12px;
}

.login-header h1 {
  font-size: 22px;
  font-weight: 700;
  margin-bottom: 4px;
}

.login-header p {
  font-size: 13px;
  color: var(--color-text-muted);
}

.login-btn {
  width: 100%;
  padding: 10px;
  font-size: 15px;
  margin-top: 8px;
}
</style>
