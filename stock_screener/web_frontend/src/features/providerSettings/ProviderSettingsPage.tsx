import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api'

type ProviderField = {
  key: string
  label: string
  secret: boolean
  input_type: string
  placeholder: string
  value: string
  is_set: boolean
  options: { value: string; label: string }[]
}

type ProviderGroup = {
  key: string
  label: string
  description: string
  configured: boolean
  testable: boolean
  fields: ProviderField[]
}

type ProviderSettingsResponse = {
  encryption_ready: boolean
  groups: ProviderGroup[]
}

const SECTIONS = [
  { title: '大模型', groupKeys: ['llm_routing', 'llm_openai', 'llm_codex', 'llm_deepseek'] },
  { title: '新闻搜索', groupKeys: ['search_routing', 'search_tavily', 'search_zhipu'] },
  { title: '数据服务', groupKeys: ['data_fred', 'data_funda', 'market_intel_routing'] },
  { title: '飞书', groupKeys: ['feishu_webhook', 'feishu_app'] },
]

export function ProviderSettingsPage() {
  const [settings, setSettings] = useState<ProviderSettingsResponse | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({})
  const [cleared, setCleared] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadSettings = useCallback(async () => {
    setError('')
    const result = await api<ProviderSettingsResponse>('/api/settings/providers')
    setSettings(result)
    const nextDrafts: Record<string, Record<string, string>> = {}
    for (const group of result.groups) {
      nextDrafts[group.key] = Object.fromEntries(group.fields.map(field => [field.key, field.secret ? '' : field.value || '']))
    }
    setDrafts(nextDrafts)
    setCleared({})
  }, [])

  useEffect(() => {
    loadSettings().catch(err => setError(err instanceof Error ? err.message : '读取配置失败'))
  }, [loadSettings])

  const groupsByKey = useMemo(() => new Map((settings?.groups || []).map(group => [group.key, group])), [settings])

  function setFieldValue(groupKey: string, fieldKey: string, value: string) {
    setDrafts(current => ({ ...current, [groupKey]: { ...(current[groupKey] || {}), [fieldKey]: value } }))
    setCleared(current => ({ ...current, [groupKey]: (current[groupKey] || []).filter(key => key !== fieldKey) }))
  }

  function toggleClear(groupKey: string, fieldKey: string) {
    setCleared(current => {
      const values = current[groupKey] || []
      return { ...current, [groupKey]: values.includes(fieldKey) ? values.filter(key => key !== fieldKey) : [...values, fieldKey] }
    })
    setDrafts(current => ({ ...current, [groupKey]: { ...(current[groupKey] || {}), [fieldKey]: '' } }))
  }

  async function save(group: ProviderGroup) {
    setBusy(current => ({ ...current, [group.key]: 'save' }))
    setError('')
    setNotice('')
    try {
      await api(`/api/settings/providers/${group.key}`, {
        method: 'PUT',
        body: JSON.stringify({ values: drafts[group.key] || {}, clear_fields: cleared[group.key] || [] }),
      })
      setNotice(`${group.label}配置已保存`)
      await loadSettings()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setBusy(current => ({ ...current, [group.key]: '' }))
    }
  }

  async function test(group: ProviderGroup) {
    setBusy(current => ({ ...current, [group.key]: 'test' }))
    setError('')
    setNotice('')
    try {
      const result = await api<{ ok: boolean; message: string }>(`/api/settings/providers/${group.key}/test`, {
        method: 'POST',
        body: JSON.stringify({ values: drafts[group.key] || {}, clear_fields: cleared[group.key] || [] }),
      })
      if (result.ok) setNotice(`${group.label}：${result.message}`)
      else setError(`${group.label}：${result.message}`)
    } catch (err) {
      setError(`${group.label}：${err instanceof Error ? err.message : '连接测试失败'}`)
    } finally {
      setBusy(current => ({ ...current, [group.key]: '' }))
    }
  }

  return <div className="provider-settings-page">
    <header className="page-header">
      <p className="eyebrow">ACCOUNT SETTINGS</p>
      <h1>API 配置</h1>
      <p>配置当前登录账号使用的大模型、新闻搜索、数据服务和飞书凭证。</p>
    </header>

    {!settings && !error && <div className="panel provider-settings-state">正在读取配置…</div>}
    {error && <div className="provider-settings-message error" role="alert">{error}</div>}
    {notice && <div className="provider-settings-message success" role="status">{notice}</div>}
    {settings && !settings.encryption_ready && <div className="provider-settings-message warning" role="status">
      服务端尚未配置 <code>ACCOUNT_CONFIG_ENCRYPTION_KEY</code>。先在服务端设置独立加密密钥，再保存账号配置。
    </div>}

    {settings && SECTIONS.map(section => <section className="provider-settings-section" key={section.title}>
      <div className="provider-settings-section-heading"><h2>{section.title}</h2></div>
      <div className="provider-settings-grid">
        {section.groupKeys.map(key => {
          const group = groupsByKey.get(key)
          if (!group) return null
          const working = Boolean(busy[group.key])
          return <article className="panel provider-config-card" key={group.key}>
            <div className="provider-config-heading">
              <div><h3>{group.label}</h3><p>{group.description}</p></div>
              <span className={`provider-config-status ${group.configured ? 'is-configured' : ''}`}>
                {group.configured ? '已配置' : '未配置'}
              </span>
            </div>
            <div className="provider-config-fields">
              {group.fields.map(field => <label className="field provider-config-field" key={field.key}>
                <span>{field.label}{field.secret && field.is_set && !cleared[group.key]?.includes(field.key) && <small>已保存</small>}</span>
                {field.input_type === 'select' ? <select
                  value={drafts[group.key]?.[field.key] || ''}
                  onChange={event => setFieldValue(group.key, field.key, event.target.value)}
                  disabled={!settings.encryption_ready || working}
                >
                  <option value="">使用默认值</option>
                  {field.options.map(option => <option value={option.value} key={option.value}>{option.label}</option>)}
                </select> : <input
                  type={field.secret ? 'password' : 'text'}
                  autoComplete="off"
                  value={drafts[group.key]?.[field.key] || ''}
                  placeholder={field.secret && field.is_set ? '已保存，留空保持不变' : field.placeholder}
                  onChange={event => setFieldValue(group.key, field.key, event.target.value)}
                  disabled={!settings.encryption_ready || working}
                />}
                {field.secret && field.is_set && <button
                  className={`provider-clear-secret ${cleared[group.key]?.includes(field.key) ? 'selected' : ''}`}
                  type="button"
                  onClick={() => toggleClear(group.key, field.key)}
                  disabled={!settings.encryption_ready || working}
                >{cleared[group.key]?.includes(field.key) ? '取消清除' : '清除已保存凭证'}</button>}
              </label>)}
            </div>
            <div className="provider-config-actions">
              {group.testable && <button className="secondary" type="button" onClick={() => test(group)} disabled={!settings.encryption_ready || working}>
                {busy[group.key] === 'test' ? '正在测试…' : '测试连接'}
              </button>}
              <button className="primary" type="button" onClick={() => save(group)} disabled={!settings.encryption_ready || working}>
                {busy[group.key] === 'save' ? '正在保存…' : '保存配置'}
              </button>
            </div>
          </article>
        })}
      </div>
    </section>)}
    <p className="provider-settings-footnote">密钥只显示保存状态，不会回传原文。账号任务读取本账号配置；未配置时不会借用其他账号或系统环境中的 Provider 密钥。</p>
  </div>
}
