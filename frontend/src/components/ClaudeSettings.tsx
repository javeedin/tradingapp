import { useState, useEffect } from 'react'

interface ClaudeSettingsProps {
  onClose: () => void
}

export default function ClaudeSettings({ onClose }: ClaudeSettingsProps) {
  const [apiKey, setApiKey] = useState('')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const saved = localStorage.getItem('claude_api_key')
    if (saved) {
      setApiKey('•'.repeat(20))
    }
  }, [])

  const handleSave = () => {
    if (!apiKey || apiKey.includes('•')) {
      setError('Please enter a valid API key')
      return
    }
    try {
      localStorage.setItem('claude_api_key', apiKey)
      setSaved(true)
      setError(null)
      setTimeout(() => {
        setSaved(false)
        onClose()
      }, 1500)
    } catch (err) {
      setError('Failed to save API key')
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span>Claude API Configuration</span>
          <button className="close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="form-group">
            <label>Claude API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value)
                setSaved(false)
              }}
              placeholder="sk-ant-..."
              style={{ width: '100%', marginTop: 8 }}
            />
            <div style={{ fontSize: 12, marginTop: 8, color: 'var(--text-secondary)' }}>
              Your API key is stored locally in your browser and never sent to our servers.
            </div>
          </div>

          {error && (
            <div className="notice error" style={{ marginTop: 12 }}>
              {error}
            </div>
          )}

          {saved && (
            <div className="notice ok" style={{ marginTop: 12 }}>
              API key saved successfully
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={handleSave}>
            Save API Key
          </button>
        </div>
      </div>
    </div>
  )
}
