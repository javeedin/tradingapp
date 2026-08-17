import type { EngineEvent } from '../types'

const KIND_COLOR: Record<string, string> = {
  entry: 'var(--green)',
  exit: 'var(--accent)',
  error: 'var(--red)',
  kill_switch: 'var(--red)',
  squareoff: 'var(--amber)',
  session: 'var(--accent)',
  backfill: 'var(--text-dim)',
}

export default function EventLog({ events }: { events: EngineEvent[] }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <span>Engine Log</span>
      </div>
      <div className="panel-body flush">
        {events.length === 0 ? (
          <div className="empty">No activity yet</div>
        ) : (
          <div className="event-log">
            {events.map((e, i) => (
              <div className="event" key={`${e.timestamp}-${i}`}>
                <span className="event-time">{e.timestamp.slice(11, 19)}</span>
                <span
                  className="event-kind"
                  style={{ color: KIND_COLOR[e.kind] ?? 'var(--text-faint)' }}
                >
                  {e.kind}
                </span>
                <span>{e.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
