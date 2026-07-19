import type {
  AuthData, Detection, Vehicle, Stats,
  Paginated, DetectionFilters, VehicleForm,
} from './types'
import { getToken, clearAuth } from './auth'

function validateBase(url: string): string {
  const origin = url.replace(/\/$/, '')
  try {
    const parsed = new URL(origin)
    const hostname = parsed.hostname
    
    // Allow localhost and loopback
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
      return origin
    }
    
    // Allow private networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    const isPrivate = 
      /^10\./.test(hostname) ||
      /^192\.168\./.test(hostname) ||
      /^172\.(1[6-9]|2[0-9]|3[0-1])\./.test(hostname)
      
    if (isPrivate) {
      return origin
    }
    
    // Allow current window host if in browser
    if (typeof window !== 'undefined' && hostname === window.location.hostname) {
      return origin
    }
  } catch (e) {
    // ignore
  }
  
  throw new Error(`Blocked request to untrusted origin: ${origin}`)
}

let rawUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

if (typeof window !== 'undefined') {
  const host = window.location.hostname;
  if (rawUrl.includes('localhost') && host !== 'localhost' && host !== '127.0.0.1') {
    rawUrl = `${window.location.protocol}//${host}:8000`;
  }
}

let BASE: string;
try {
  BASE = validateBase(rawUrl);
} catch {
  // Fallback to same-origin backend if validation fails (e.g. public IP)
  BASE = typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : 'http://localhost:8000';
}

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'

async function req<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const token = getToken()
  const res   = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })

  if (res.status === 401) {
    clearAuth()
    if (typeof window !== 'undefined') window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({})) as { detail?: string }
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }

  if (res.status === 204) return null as T
  return res.json() as Promise<T>
}

function qs(params: Record<string, unknown>): string {
  return Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join('&')
}

export const api = {
  /* Auth */
  login: (username: string, password: string) =>
    req<AuthData>('POST', '/api/auth/login/json', { username, password }),

  /* Stats */
  stats: () => req<Stats>('GET', '/api/stats'),

  /* Detections */
  detections: (params?: DetectionFilters & { page?: number; per_page?: number }) =>
    req<Paginated<Detection>>('GET', `/api/detections?${qs((params ?? {}) as Record<string, unknown>)}`),
  delDetect: (id: number) =>
    req<{ message: string; id: number }>('DELETE', `/api/detections/${id}`),

  /* Alerts */
  alerts: (params?: { page?: number; per_page?: number }) =>
    req<Paginated<Detection>>('GET', `/api/alerts?${qs(params ?? {})}`),

  /* Vehicles */
  vehicles: (params?: {
    page?: number; per_page?: number
    search?: string; is_authorized?: number | string
  }) =>
    req<Paginated<Vehicle>>('GET', `/api/vehicles?${qs(params ?? {})}`),
  createVehicle: (data: VehicleForm) => req<Vehicle>('POST', '/api/vehicles', data),
  updateVehicle: (id: number, data: Partial<VehicleForm>) =>
    req<Vehicle>('PUT', `/api/vehicles/${id}`, data),
  delVehicle: (id: number) =>
    req<{ message: string; id: number }>('DELETE', `/api/vehicles/${id}`),

  /* Quick register from live detection */
  registerPlate: (data: {
    license_number: string
    owner_name:     string
    make?:          string
    model?:         string
    color?:         string
    owner_cnic?:    string
    dues?:          string
    status?:        string
  }) => req<{ message: string; vehicle: Vehicle }>('POST', '/api/register', data),

  changePassword: (current_password: string, new_password: string) =>
    req<{ message: string }>('POST', '/api/auth/change-password', { current_password, new_password }),

  exportDetections: (params?: DetectionFilters) => {
    const token = getToken()
    const query = qs((params ?? {}) as Record<string, unknown>)
    const url = `${BASE}/api/detections/export${query ? '?' + query : ''}`
    const a = document.createElement('a')
    a.href = url
    // Attach token via a short-lived fetch so the browser downloads the file
    return fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(res => {
        if (!res.ok) throw new Error(`Export failed: HTTP ${res.status}`)
        return res.blob()
      })
      .then(blob => {
        const objUrl = URL.createObjectURL(blob)
        a.href = objUrl
        a.download = 'detections.csv'
        a.click()
        URL.revokeObjectURL(objUrl)
      })
  },
}