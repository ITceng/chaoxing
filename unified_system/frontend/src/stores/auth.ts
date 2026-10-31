import { defineStore } from 'pinia'
import api from '../services/api'
import type { User } from '../types'

interface AuthPayload {
  user: User
  roles?: string[]
  permissions?: string[]
  csrf: string
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    roles: [] as string[],
    permissions: [] as string[],
    csrf: '',
    initialized: false,
    initializing: false,
  }),
  getters: {
    isSuperadmin: state => state.roles.includes('superadmin'),
    isAdmin: state => state.roles.includes('admin') || state.roles.includes('superadmin'),
    can: state => (permission: string) => state.roles.includes('superadmin') || state.permissions.includes(permission),
  },
  actions: {
    setAuth(data: AuthPayload) {
      this.user = data.user
      this.roles = data.roles || data.user.roles || []
      this.permissions = data.permissions || []
      this.csrf = data.csrf || ''
      this.initialized = true
    },
    clearAuth() {
      this.user = null
      this.roles = []
      this.permissions = []
      this.csrf = ''
      this.initialized = true
    },
    async initialize() {
      if (this.initialized || this.initializing) return
      this.initializing = true
      try {
        const { data } = await api.get<AuthPayload>('/auth/me')
        this.setAuth(data)
      } catch (error: any) {
        if (error.response?.status === 401) this.clearAuth()
        else throw error
      } finally {
        this.initializing = false
      }
    },
    async login(email: string, password: string) {
      const { data } = await api.post<AuthPayload>('/auth/login', { email, password })
      this.setAuth(data)
      const { data: me } = await api.get<AuthPayload>('/auth/me')
      this.setAuth(me)
    },
    async register(payload: { email: string; display_name: string; password: string; invitation_code: string }) {
      const { data } = await api.post<AuthPayload>('/auth/register', payload)
      this.setAuth(data)
      const { data: me } = await api.get<AuthPayload>('/auth/me')
      this.setAuth(me)
    },
    async logout() {
      try { await api.post('/auth/logout', {}) }
      finally { this.clearAuth() }
    },
  },
})
