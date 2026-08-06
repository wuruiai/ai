/** 从 API 错误里提取可读信息（兼容统一 envelope 与旧 detail 两种格式）。 */
export function extractError(e: any, fallback = '请求失败'): string {
  return (
    e?.response?.data?.error?.message ||
    e?.response?.data?.detail ||
    e?.message ||
    fallback
  )
}
