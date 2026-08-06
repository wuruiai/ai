import client from './client'

export interface HealthInfo {
  status: string
  /** 版本单一来源 backend/__init__.py.__version__，经 /health 暴露（M12 前端对齐后端） */
  version: string
  schema_version: number
}

/**
 * 后端存活探针：版本号从此处取，保证前端展示与后端部署版本一致（G10.9 M12）。
 * /health 在根路径（非 /api/v1），需覆盖 client 默认 baseURL。
 */
export async function getHealth(): Promise<HealthInfo> {
  const res = await client.get('/health', { baseURL: '/' })
  return res.data as HealthInfo
}
