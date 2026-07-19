import type { User, AuthData } from './types'

const TOKEN_KEY = 'anpr_token'
const USER_KEY  = 'anpr_user'

export const isAuthed = (): boolean =>
  typeof window !== 'undefined' && !!localStorage.getItem(TOKEN_KEY)

export const getUser = (): User | null => {
  if (typeof window === 'undefined') return null
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) ?? 'null') as User | null
  } catch {
    return null
  }
}

export const setAuth = (data: AuthData): void => {
  localStorage.setItem(TOKEN_KEY, data.access_token)
  localStorage.setItem(USER_KEY, JSON.stringify({ username: data.username, role: data.role }))
}

export const clearAuth = (): void => {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export const getToken = (): string | null =>
  typeof window !== 'undefined' ? localStorage.getItem(TOKEN_KEY) : null

export const isAdmin = (): boolean => getUser()?.role === 'admin'