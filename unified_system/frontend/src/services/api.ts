import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { useAuthStore } from '../stores/auth'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  withCredentials: true,
  timeout: 20000,
  headers: { Accept: 'application/json' },
})

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const method = (config.method || 'get').toLowerCase()
  if (!['get', 'head', 'options'].includes(method)) {
    const auth = useAuthStore()
    if (auth.csrf) config.headers.set('X-CSRF-Token', auth.csrf)
  }
  return config
})

api.interceptors.response.use(
  response => response,
  (error: AxiosError<{ error?: { message?: string } }>) => {
    const path = error.config?.url || ''
    if (error.response?.status === 401 && !/\/auth\/(login|register|reset-password)$/.test(path)) {
      const auth = useAuthStore()
      if (auth.user) {
        auth.clearAuth()
        window.dispatchEvent(new CustomEvent('session-expired'))
      }
    }
    const detail = error.response?.data?.error?.message
    if (detail) error.message = detail
    else if (!error.response) error.message = '网络暂不可用，请检查连接后重试'
    return Promise.reject(error)
  },
)

export default api
