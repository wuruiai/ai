import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}))

import client from '@/api/client'
import { getHealth } from '@/api/system'

const mockedGet = client.get as unknown as ReturnType<typeof vi.fn>

describe('getHealth（G10.9 M12 前端版本对齐后端）', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('命中根路径 /health（覆盖 client 默认 /api/v1 baseURL），返回后端版本', async () => {
    mockedGet.mockResolvedValue({
      data: { status: 'ok', version: '1.1.0', schema_version: 1 },
    })

    const health = await getHealth()

    expect(health.status).toBe('ok')
    expect(health.version).toBe('1.1.0')
    // /health 在根路径，不能走 /api/v1 前缀
    expect(mockedGet).toHaveBeenCalledWith('/health', { baseURL: '/' })
  })
})
