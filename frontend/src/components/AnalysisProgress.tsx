interface AnalysisProgressProps {
  isOpen: boolean
  steps: { name: string; completed: boolean; error?: string }[]
}

export default function AnalysisProgress({ isOpen, steps }: AnalysisProgressProps) {
  if (!isOpen) return null

  const allCompleted = steps.every((s) => s.completed || s.error)
  const hasError = steps.some((s) => s.error)

  return (
    <div className="modal-overlay" onClick={(e) => e.stopPropagation()}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 400 }}>
        <div className="modal-head">
          <span>Analyzing Option Chain</span>
        </div>
        <div className="modal-body">
          <div style={{ display: 'grid', gap: 12 }}>
            {steps.map((step, idx) => (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: 12,
                  background: 'var(--panel-alt)',
                  borderRadius: 8,
                  borderLeft: `3px solid ${
                    step.error ? 'var(--red)' : step.completed ? 'var(--green)' : 'var(--border)'
                  }`,
                }}
              >
                <div style={{ fontSize: 20, minWidth: 24 }}>
                  {step.error ? '❌' : step.completed ? '✅' : '⏳'}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{step.name}</div>
                  {step.error && (
                    <div
                      style={{
                        fontSize: 11,
                        color: 'var(--red)',
                        marginTop: 4,
                      }}
                    >
                      {step.error}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          {!allCompleted && (
            <div
              style={{
                marginTop: 20,
                textAlign: 'center',
                fontSize: 12,
                color: 'var(--text-dim)',
              }}
            >
              <div
                style={{
                  display: 'inline-block',
                  width: 20,
                  height: 20,
                  border: '2px solid var(--accent)',
                  borderTop: '2px solid transparent',
                  borderRadius: '50%',
                  animation: 'spin 1s linear infinite',
                }}
              />
              <div style={{ marginTop: 8 }}>Connecting to Claude AI...</div>
            </div>
          )}

          {hasError && (
            <div className="notice error" style={{ marginTop: 16, marginBottom: 0 }}>
              Analysis failed. Please try again.
            </div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}
