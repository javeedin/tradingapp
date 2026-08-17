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
  const apiKey = localStorage.getItem('claude_api_key')
  if (!apiKey) {
    throw new Error('Claude API key not configured. Please set it in settings.')
  }

  const prompt = buildAnalysisPrompt(chain)

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-sonnet-5',
      max_tokens: 1024,
      messages: [
        {
          role: 'user',
          content: prompt,
        },
      ],
    }),
  })

  if (!response.ok) {
    let errorDetail = 'Failed to analyze with Claude'
    try {
      const error = await response.json()
      errorDetail = error.error?.message || error.detail || JSON.stringify(error, null, 2)
    } catch {
      errorDetail = await response.text()
    }
    throw new Error(`Claude API error:\n\n${errorDetail}`)
  }

  const data = await response.json()
  const content = data.content[0]?.text || ''

  return parseClaudeResponse(content, JSON.stringify(data, null, 2))
}

function buildAnalysisPrompt(chain: OptionChainResponse): string {
  const atmStrike = chain.rows.reduce(
    (best, row) =>
      Math.abs(row.strike - (chain.spot || 0)) < Math.abs(best - (chain.spot || 0))
        ? row.strike
        : best,
    chain.rows[0]?.strike || 0,
  )

  const rows = chain.rows.slice(
    Math.max(0, chain.rows.findIndex((r) => r.strike === atmStrike) - 5),
    Math.min(chain.rows.length, chain.rows.findIndex((r) => r.strike === atmStrike) + 6),
  )

  const chainData = rows
    .map((row) => {
      const callData = row.call
        ? `C:${row.call.ltp ?? '—'}(${row.call.open_interest ?? 0})`
        : 'C:—'
      const putData = row.put
        ? `P:${row.put.ltp ?? '—'}(${row.put.open_interest ?? 0})`
        : 'P:—'
      const isAtm = row.strike === atmStrike ? ' <-- ATM' : ''
      return `${row.strike}: ${callData} ${putData}${isAtm}`
    })
    .join('\n')

  return `
Analyze this option chain for ${chain.symbol} expiring on ${chain.expiry}:

Current Spot: ${chain.spot}
Lot Size: ${chain.lot_size}

Option Chain Data (Call & Put LTP with OI in parentheses):
${chainData}

Please provide:
1. Market movement assessment (bullish/bearish/neutral) with reason
2. 3-5 suggested strike prices with reasoning
3. Recommended strategy (vertical spread, straddle, strangle, iron condor, etc.)
4. Risk level assessment

Format your response as JSON only with this structure:
{
  "marketMovement": "bullish|bearish|neutral",
  "direction": "bullish|bearish|neutral",
  "suggestedStrikes": [
    {
      "strike": number,
      "confidence": 0-1,
      "strategy": "strategy name",
      "reasoning": "why this strike",
      "side": "call|put"
    }
  ],
  "reasoning": "overall market assessment",
  "riskLevel": "low|medium|high"
}
`
}

function parseClaudeResponse(content: string, fullResponse: string = ''): ClaudeAnalysisResult {
  try {
    if (!content || content.trim() === '') {
      throw new Error('Claude API returned empty response text')
    }

    const jsonMatch = content.match(/\{[\s\S]*\}/)
    if (!jsonMatch) {
      throw new Error(`No JSON found in response. Claude returned:\n\n${content}`)
    }

    const parsed = JSON.parse(jsonMatch[0])

    return {
      marketMovement: parsed.marketMovement || 'neutral',
      direction: parsed.direction || 'neutral',
      suggestedStrikes: (parsed.suggestedStrikes || []).map((s: any) => ({
        strike: s.strike,
        confidence: s.confidence || 0.5,
        strategy: s.strategy || 'Unknown',
        reasoning: s.reasoning || '',
        side: s.side || 'call',
      })),
      reasoning: parsed.reasoning || 'Analysis complete',
      riskLevel: parsed.riskLevel || 'medium',
    }
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : 'Unknown error'
    const debugInfo = fullResponse ? `\n\nExtracted text:\n${content}\n\nFull API response:\n${fullResponse}` : `\n\nResponse text:\n${content || '(empty)'}`
    throw new Error(`Failed to parse Claude response:\n${errorMsg}${debugInfo}`)
  }
}
