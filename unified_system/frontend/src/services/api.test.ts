import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import api from './api'
import { useAuthStore } from '../stores/auth'

let captured: any

beforeEach(() => {
  setActivePinia(createPinia())
  captured = null
  api.defaults.adapter = async config => {
    captured = config
    return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }
  }
})

afterEach(() => {
  delete api.defaults.adapter
})

describe('shared API client security defaults', () => {
  it('uses a same-origin API base and sends session cookies', () => {
    expect(api.defaults.baseURL).toBe('/api/v1')
    expect(api.defaults.withCredentials).toBe(true)
  })

  it('attaches the in-memory CSRF token to write requests', async () => {
    useAuthStore().csrf = 'csrf-token-in-memory'
    await api.post('/profile', { display_name: 'New name' })
    expect(captured.headers.get('X-CSRF-Token')).toBe('csrf-token-in-memory')
    expect(captured.url).toBe('/profile')
  })

  it('does not add a CSRF header to safe GET requests', async () => {
    useAuthStore().csrf = 'csrf-token-in-memory'
    await api.get('/auth/me')
    expect(captured.headers.get('X-CSRF-Token')).toBeUndefined()
  })
})
