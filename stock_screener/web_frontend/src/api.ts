export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  })
  const payload = await response.json().catch(() => null)
  if (payload?.ok === false) {
    throw new Error(payload.message || '请求失败，请稍后重试')
  }
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || `${response.status} ${response.statusText}`)
  }
  return payload as T
}
