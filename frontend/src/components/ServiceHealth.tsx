import type { Health } from '../api'

export function ServiceHealth({ health }: { health: Health }) {
  const appearance = {
    ready: 'bg-teal-950 text-teal-300',
    starting: 'bg-amber-950 text-amber-200',
    unavailable: 'bg-rose-950 text-rose-200',
  }
  return <span className={`rounded-full px-3 py-1 text-sm font-medium capitalize ${appearance[health.status]}`}>
    {health.status}
  </span>
}
