import { createRouter, createWebHistory } from 'vue-router'

import LoginPage from '../views/LoginPage.vue'
import DashboardPage from '../views/DashboardPage.vue'
import RegistrationListPage from '../views/RegistrationListPage.vue'
import RegistrationWizardPage from '../views/RegistrationWizardPage.vue'
import ReviewListPage from '../views/ReviewListPage.vue'
import RegistrationReviewPage from '../views/RegistrationReviewPage.vue'
import FinanceDashboardPage from '../views/FinanceDashboardPage.vue'
import ReportsPage from '../views/ReportsPage.vue'
import AdminPage from '../views/AdminPage.vue'
import AppLayout from '../components/AppLayout.vue'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: LoginPage,
    meta: { public: true },
  },
  {
    path: '/',
    component: AppLayout,
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'Dashboard',
        component: DashboardPage,
        meta: { roles: ['applicant', 'reviewer', 'financial_admin', 'system_admin'] },
      },
      {
        path: 'registrations',
        name: 'RegistrationList',
        component: RegistrationListPage,
        meta: { roles: ['applicant', 'reviewer', 'financial_admin', 'system_admin'] },
      },
      {
        path: 'registrations/new',
        name: 'NewRegistration',
        component: RegistrationWizardPage,
        meta: { roles: ['applicant', 'system_admin'] },
      },
      {
        path: 'registrations/:id/edit',
        name: 'EditRegistration',
        component: RegistrationWizardPage,
        meta: { roles: ['applicant', 'system_admin'] },
      },
      {
        path: 'reviews',
        name: 'ReviewList',
        component: ReviewListPage,
        meta: { roles: ['reviewer', 'system_admin'] },
      },
      {
        path: 'reviews/:id',
        name: 'RegistrationReview',
        component: RegistrationReviewPage,
        meta: { roles: ['reviewer', 'system_admin'] },
      },
      {
        path: 'finance',
        name: 'FinanceDashboard',
        component: FinanceDashboardPage,
        meta: { roles: ['financial_admin', 'system_admin'] },
      },
      {
        path: 'reports',
        name: 'Reports',
        component: ReportsPage,
        meta: { roles: ['financial_admin', 'system_admin'] },
      },
      {
        path: 'admin',
        name: 'Admin',
        component: AdminPage,
        meta: { roles: ['system_admin'] },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory('/'),
  routes,
})

// Navigation guard
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  const role = localStorage.getItem('userRole')

  // Public routes
  if (to.meta.public) {
    if (token) {
      return next({ name: 'Dashboard' })
    }
    return next()
  }

  // Auth required
  if (!token) {
    return next({ name: 'Login' })
  }

  // Role check
  if (to.meta.roles && !to.meta.roles.includes(role)) {
    return next({ name: 'Dashboard' })
  }

  next()
})

export default router
