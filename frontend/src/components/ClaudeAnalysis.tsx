import { formatNumber } from '../api'
import type { ClaudeAnalysisResult } from '../claudeAPI'

interface ClaudeAnalysisProps {
  analysis: ClaudeAnalysisResult
  onClose: () => void
  onSelectStrike: (strike: number, side: 'call' | 'put') => void
}

export default function ClaudeAnalysis({ analysis, onClose, onSelectStrike }: ClaudeAnalysisProps) {
  const directionColor =
    analysis.direction === 'bullish'
      ? 'rgba(18,138,77,0.15)'
      : analysis.direction === 'bearish'
        ? 'rgba(211,47,54,0.15)'
        : 'rgba(33,150,243,0.15)'

  const directionIcon =
    analysis.direction === 'bullish' ? '📈' : analysis.direction === 'bearish' ? '📉' : '➡️'

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 700 }}>
        <div className="modal-head">
          <span>Analysis</span>
          <button className="close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body" style={{ maxHeight: 600, overflowY: 'auto' }}>
          <div
            style={{
              padding: 16,
              background: directionColor,
              borderRadius: 8,
              marginBottom: 16,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 24 }}>{directionIcon}</span>
              <span style={{ fontSize: 18, fontWeight: 600, textTransform: 'capitalize' }}>
                {analysis.direction} Market
              </span>
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.6 }}>{analysis.reasoning}</div>
          </div>

          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, textTransform: 'uppercase', fontWeight: 600, marginBottom: 8 }}>
              Risk Level
            </div>
            <div
              style={{
                display: 'inline-block',
                padding: '4px 12px',
                borderRadius: 4,
                background:
                  analysis.riskLevel === 'high'
                    ? 'rgba(211,47,54,0.2)'
                    : analysis.riskLevel === 'medium'
                      ? 'rgba(251,188,5,0.2)'
                      : 'rgba(18,138,77,0.2)',
                textTransform: 'capitalize',
                fontSize: 12,
                fontWeight: 500,
              }}
            >
              {analysis.riskLevel}
            </div>
          </div>

          <div>
            <div style={{ fontSize: 12, textTransform: 'uppercase', fontWeight: 600, marginBottom: 12 }}>
              Suggested Strikes
            </div>
            <div style={{ display: 'grid', gap: 12 }}>
              {analysis.suggestedStrikes.map((strike, idx) => (
                <div
                  key={idx}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    padding: 12,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <div>
                      <div style={{ fontSize: 14, fontWeight: 600 }}>
                        {formatNumber(strike.strike, 0)}{' '}
                        <span
                          style={{
                            textTransform: 'uppercase',
                            fontSize: 10,
                            fontWeight: 700,
                            color:
                              strike.side === 'call'
                                ? 'rgba(18,138,77,0.8)'
                                : 'rgba(211,47,54,0.8)',
                          }}
                        >
                          {strike.side}
                        </span>
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                        {strike.strategy}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 12, fontWeight: 600 }}>
                        {(strike.confidence * 100).toFixed(0)}%
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                        Confidence
                      </div>
                    </div>
                  </div>
                  <div style={{ fontSize: 12, lineHeight: 1.5, marginBottom: 8 }}>
                    {strike.reasoning}
                  </div>
                  <button
                    className="mini primary"
                    onClick={() => onSelectStrike(strike.strike, strike.side)}
                  >
                    Trade This Strike
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
