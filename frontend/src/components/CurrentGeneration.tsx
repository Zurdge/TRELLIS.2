import { stages, type Job } from '../api'
import { JobSettings } from './JobSettings'

export function CurrentGeneration({ active, queued }: { active?: Job; queued: Job[] }) {
  return <section className="panel" aria-labelledby="current-title">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 id="current-title">Currently generating</h2>
      <span className="muted">{queued.length} waiting</span>
    </div>
    {active ? <div className="mt-4">
      <p className="wrap-anywhere font-medium">{active.filename}</p>
      <p className="muted mt-1">{active.stage ? stages[active.stage] : 'Starting generation'}</p>
      <JobSettings job={active} />
      <progress className="mt-3 block h-2 w-full" aria-label="Generation in progress" />
    </div> : <p className="muted mt-3">No generation running.</p>}
    {queued.length > 0 && <details className="mt-4 border-t border-slate-800 pt-3">
      <summary className="cursor-pointer text-sm font-medium">Queued images</summary>
      <ol className="mt-2 space-y-3">
        {queued.map(job => <li key={job.id} className="wrap-anywhere text-sm">
          <span className="font-medium">{job.filename}</span>
          <JobSettings job={job} />
        </li>)}
      </ol>
    </details>}
  </section>
}
