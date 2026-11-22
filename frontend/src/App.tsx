import { useEffect, useRef, useState, type FormEvent } from 'react'
import { defaultSettingsInput, generationSettings, readHealth, readJobs, selection, stages, submitJobs,
  type Health, type Job, type SettingsInput } from './api'
import { usePolling } from './usePolling'
import { ServiceHealth } from './components/ServiceHealth'
import { UploadForm } from './components/UploadForm'
import { CurrentGeneration } from './components/CurrentGeneration'
import { FinishedAssets } from './components/FinishedAssets'

export default function App() {
  const [health, setHealth] = useState<Health>({ status: 'starting' })
  const [jobs, setJobs] = useState<Job[]>([])
  const [stale, setStale] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [settings, setSettings] = useState<SettingsInput>(defaultSettingsInput)
  const [busy, setBusy] = useState(false)
  const [checking, setChecking] = useState(false)
  const [feedback, setFeedback] = useState('')
  const pending = useRef(new Map<string, Job>())
  const observed = useRef<Job[]>([])
  const picker = useRef<HTMLInputElement>(null)
  const submission = useRef<AbortController | null>(null)

  const refreshHealth = usePolling(readHealth, value => {
    setHealth(value)
    return 5000
  }, () => {
    setHealth({ status: 'unavailable', message: 'The generation service could not be reached.' })
    return 5000
  })
  const refreshJobs = usePolling(readJobs, values => {
    observed.current = values
    for (const value of values) pending.current.delete(value.id)
    const merged = [...pending.current.values(), ...values]
    setJobs(merged)
    setStale(false)
    setChecking(false)
    return merged.some(job => job.status === 'queued' || job.status === 'running') ? 2000 : 5000
  }, () => {
    setStale(true)
    return observed.current.some(job => job.status === 'queued' || job.status === 'running') || pending.current.size ? 2000 : 5000
  })

  useEffect(() => () => submission.current?.abort(), [])

  const valid = selection.safeParse(files)
  const validatedSettings = generationSettings.safeParse({
    pipeline_type: settings.pipeline_type,
    seed: settings.seed.trim() ? Number(settings.seed) : undefined,
    sampling_steps: settings.sampling_steps.trim() ? Number(settings.sampling_steps) : undefined,
    texture_size: Number(settings.texture_size),
    decimation_target: settings.decimation_target.trim() ? Number(settings.decimation_target) : undefined,
  })
  const enabled = valid.success && validatedSettings.success && health.status === 'ready' && !busy && !checking
  const active = jobs.find(job => job.status === 'running')
  const queued = jobs.filter(job => job.status === 'queued').reverse()
  const completed = jobs.filter(job => job.status === 'succeeded').length
  const failures = jobs.filter(job => job.status === 'failed').length

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!enabled || !validatedSettings.success || submission.current) return
    const controller = new AbortController()
    submission.current = controller
    setBusy(true)
    setFeedback('')
    try {
      const result = await submitJobs(files, validatedSettings.data, AbortSignal.any([controller.signal, AbortSignal.timeout(123_000)]))
      if (controller.signal.aborted) return
      if (result.jobs) {
        refreshJobs()
        const known = new Set(observed.current.map(item => item.id))
        for (const job of result.jobs.slice().reverse()) {
          if (!known.has(job.id)) pending.current.set(job.id, job)
        }
        setJobs([...pending.current.values(), ...observed.current])
        setFeedback(`${result.jobs.length} ${result.jobs.length === 1 ? 'image' : 'images'} queued.`)
        setFiles([])
        if (picker.current) picker.current.value = ''
      } else {
        setFeedback(result.error ?? 'The service rejected the batch.')
        refreshHealth()
      }
    } catch {
      if (controller.signal.aborted) return
      setChecking(true)
      setFeedback('Submission unconfirmed. Check generation history for these files before retrying to avoid duplicates.')
      refreshJobs()
    } finally {
      submission.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return <main className="mx-auto max-w-3xl space-y-4 px-4 py-8 sm:px-8 sm:py-12">
    <header className="mb-7 flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-3xl font-semibold tracking-tight">TRELLIS.2</h1>
      <ServiceHealth health={health} />
      {health.status === 'unavailable' && <p className="muted w-full">{health.message}</p>}
    </header>
    <p className="sr-only" aria-live="polite">
      Generator {health.status}. {active ? `${active.filename}, ${active.stage ? stages[active.stage] : 'Starting generation'}.` : 'No generation running.'}
      {' '}{queued.length} waiting. {completed} completed. {failures} failed.
    </p>
    <UploadForm files={files} picker={picker} busy={busy} enabled={enabled} feedback={feedback}
      validation={files.length && !valid.success ? valid.error.issues[0].message : undefined}
      settings={settings}
      invalidSettings={validatedSettings.success ? [] : validatedSettings.error.issues.map(issue => issue.path[0])}
      changeSetting={(key, value) => { setSettings(current => ({ ...current, [key]: value })); setFeedback('') }}
      resetSettings={() => { setSettings(defaultSettingsInput); setFeedback('') }}
      choose={next => { setFiles(next); setFeedback('') }} submit={submit} />
    {stale && <p className="muted px-2" role="status">Job history is temporarily unavailable. Showing the last received history.</p>}
    <CurrentGeneration active={active} queued={queued} />
    <FinishedAssets jobs={jobs} />
  </main>
}
