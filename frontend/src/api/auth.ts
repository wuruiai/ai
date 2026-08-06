import client from './client'

export interface AuthUser {
  user_id: string
  username: string
  role: string
  display_name?: string
}

export interface AuthResponse {
  token: string
  user: AuthUser
}

export async function register(username: string, password: string, displayName?: string): Promise<AuthResponse> {
  const res = await client.post('/auth/register', {
    username,
    password,
    display_name: displayName || undefined,
  })
  return res.data as AuthResponse
}

export async function login(username: string, password: string): Promise<AuthResponse> {
  const res = await client.post('/auth/login', { username, password })
  return res.data as AuthResponse
}

export async function me(): Promise<AuthUser> {
  const res = await client.get('/auth/me')
  return res.data as AuthUser
}
