interface ClaudeSettingsProps {
  onClose: () => void
}

export default function ClaudeSettings({ onClose }: ClaudeSettingsProps) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span>Claude AI Configuration</span>
          <button className="close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div style={{ lineHeight: 1.6 }}>
            <h3 style={{ marginTop: 0 }}>Claude AI Setup</h3>
            <p>
              Claude AI analysis for option chains is configured on the server side. To enable it:
            </p>
            <ol>
              <li>Get your Claude API key from <a href="https://console.anthropic.com" target="_blank" rel="noreferrer">console.anthropic.com</a></li>
              <li>Set the environment variable: <code style={{ background: 'var(--panel-alt)', padding: '4px 8px', borderRadius: 4 }}>CLAUDE_API_KEY=sk-ant-...</code></li>
              <li>Restart the backend service</li>
            </ol>
            <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>
              Your API key is kept secure on the server and never exposed to the browser.
            </p>
          </div>
        </div>
        <div className="modal-foot">
          <button className="primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
