import { stages, type Job } from '../api'

export function CurrentGeneration({ active, waiting }: { active?: Job; waiting: number }) {
  return <section className="panel" aria-labelledby="current-title">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 id="current-title">Currently generating</h2>
      <span className="muted">{waiting} waiting</span>
    </div>
    {active ? <div className="mt-4">
      <p className="wrap-anywhere font-medium">{active.filename}</p>
      <p className="muted mt-1">{active.stage ? stages[active.stage] : 'Starting generation'}</p>
      <progress className="mt-3 block h-2 w-full" aria-label="Generation in progress" />
    </div> : <p className="muted mt-3">No generation running.</p>}
  </section>
}
