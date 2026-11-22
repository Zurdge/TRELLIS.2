import type { Job } from '../api'

export function FinishedAssets({ jobs }: { jobs: Job[] }) {
  const completed = jobs.filter(job => job.status === 'succeeded')
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
  const failed = jobs.filter(job => job.status === 'failed')

  return <section className="panel" aria-labelledby="assets-title">
    <h2 id="assets-title">Finished assets</h2>
    {completed.length === 0 ? <p className="muted mt-3">Completed models will appear here.</p> :
      <ul className="mt-2 divide-y divide-slate-800">
        {completed.map(job => <li key={job.id} className="flex flex-wrap items-center justify-between gap-3 py-4">
          <div className="min-w-0">
            <p className="wrap-anywhere font-medium">{job.filename}</p>
            <time className="muted" dateTime={job.updated_at}>{new Date(job.updated_at).toLocaleString()}</time>
          </div>
          <a className="rounded-lg border border-teal-800 px-3 py-2 text-sm font-semibold text-teal-300 hover:bg-teal-950"
            aria-label={`Download GLB for ${job.filename}`} href={`/api${job.download_url}`}>Download GLB</a>
        </li>)}
      </ul>}
    {failed.length > 0 && <details className="mt-4 border-t border-slate-800 pt-4">
      <summary className="cursor-pointer text-sm font-medium text-rose-300">Failed generations ({failed.length})</summary>
      <ul className="mt-3 space-y-3">
        {failed.map(job => <li key={job.id} className="wrap-anywhere text-sm"><strong>{job.filename}</strong><p className="muted">{job.error}</p></li>)}
      </ul>
    </details>}
  </section>
}
