import client from './client'

export interface AuthUser {
  user_id: string
  username: string
  role: string
  display_name?: string
}

export interface AuthResponse {
  /** 兼容旧前端别名 = access_token */
  token?: string
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: AuthUser
}

export async function register(
  username: string,
  password: string,
  displayName?: string
): Promise<AuthResponse> {
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

/** 用 refresh token 换新 token 对（后端已实现轮换 + 重放检测）。 */
export async function refresh(refreshToken: string): Promise<AuthResponse> {
  const res = await client.post('/auth/refresh', { refresh_token: refreshToken })
  return res.data as AuthResponse
}

/** 登出：吊销服务端 refresh token（all=true 吊销该用户全部设备）。 */
export async function logout(refreshToken: string, all = false): Promise<void> {
  await client.post('/auth/logout', { refresh_token: refreshToken, all })
}

export async function me(): Promise<AuthUser> {
  const res = await client.get('/auth/me')
  return res.data as AuthUser
}
