import type { OptionChainResponse } from './types'

export interface ClaudeAnalysisResult {
  marketMovement: string
  direction: 'bullish' | 'bearish' | 'neutral'
  suggestedStrikes: SuggestedStrike[]
  reasoning: string
  riskLevel: 'low' | 'medium' | 'high'
}

export interface SuggestedStrike {
  strike: number
  confidence: number
  strategy: string
  reasoning: string
  side: 'call' | 'put'
}

export async function analyzeOptionChain(
  chain: OptionChainResponse,
): Promise<ClaudeAnalysisResult> {
  const response = await fetch('/api/claude/analyze', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ chain }),
  })

  if (!response.ok) {
    let errorDetail = 'Failed to analyze with Claude'
    try {
      const error = await response.json()
      errorDetail = error.detail || JSON.stringify(error, null, 2)
    } catch {
      errorDetail = await response.text()
    }
    throw new Error(errorDetail)
  }

  const data = await response.json()

  return {
    marketMovement: data.marketMovement || 'neutral',
    direction: data.direction || 'neutral',
    suggestedStrikes: (data.suggestedStrikes || []).map((s: any) => ({
      strike: s.strike,
      confidence: s.confidence || 0.5,
      strategy: s.strategy || 'Unknown',
      reasoning: s.reasoning || '',
      side: s.side || 'call',
    })),
    reasoning: data.reasoning || 'Analysis complete',
    riskLevel: data.riskLevel || 'medium',
  }
}
