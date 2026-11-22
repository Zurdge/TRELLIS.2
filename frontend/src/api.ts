import { z } from 'zod'

export const stages = {
  preparing_image: 'Preparing image',
  extracting_features: 'Extracting image features',
  generating_structure: 'Generating structure',
  generating_shape: 'Generating shape',
  generating_materials: 'Generating materials',
  decoding_mesh: 'Decoding mesh',
  exporting_glb: 'Exporting GLB',
} as const

const health = z.discriminatedUnion('status', [
  z.object({ status: z.literal('ready') }),
  z.object({ status: z.literal('starting') }),
  z.object({ status: z.literal('unavailable'), message: z.string().min(1) }),
])
export type Health = z.infer<typeof health>
const base = z.object({
  id: z.uuidv4(),
  filename: z.string().min(1),
  created_at: z.iso.datetime(),
  updated_at: z.iso.datetime(),
  stage: z.null(),
  download_url: z.null(),
  error: z.null(),
})
const job = z.discriminatedUnion('status', [
  base.extend({ status: z.literal('queued') }),
  base.extend({ status: z.literal('running'), stage: z.keyof(z.object(stagesToSchema())).nullable() }),
  base.extend({ status: z.literal('failed'), error: z.string().min(1) }),
  base.extend({ status: z.literal('succeeded'), download_url: z.string() }),
]).refine(value => value.status !== 'succeeded' || value.download_url === `/jobs/${value.id}/download`)

function stagesToSchema() {
  return {
    preparing_image: z.unknown(), extracting_features: z.unknown(), generating_structure: z.unknown(),
    generating_shape: z.unknown(), generating_materials: z.unknown(), decoding_mesh: z.unknown(), exporting_glb: z.unknown(),
  }
}

export type Job = z.infer<typeof job>
const snapshot = z.object({ jobs: z.array(job) }).refine(({ jobs }) =>
  new Set(jobs.map(item => item.id)).size === jobs.length && jobs.filter(item => item.status === 'running').length <= 1)

const file = z.file().min(1, 'Empty files are not accepted.').max(10 * 1024 ** 2, 'Each file must be at most 10 MiB.')
  .refine(value => value.type && value.type !== 'application/octet-stream'
    ? ['image/png', 'image/jpeg', 'image/webp'].includes(value.type)
    : /\.(png|jpe?g|webp)$/i.test(value.name), 'Choose a JPEG, PNG, or WebP image.')
export const selection = z.array(file).min(1).max(8, 'Choose up to 8 images.')

export async function readHealth(signal: AbortSignal): Promise<Health> {
  const response = await fetch('/api/health', { signal })
  const data = health.parse(await response.json())
  if ((response.status === 200 && data.status === 'ready') || (response.status === 503 && data.status !== 'ready')) return data
  throw new Error('Unexpected health response')
}

export async function readJobs(signal: AbortSignal) {
  const response = await fetch('/api/jobs', { signal })
  if (response.status !== 200) throw new Error('Job history is unavailable')
  return snapshot.parse(await response.json()).jobs
}

const rejection = z.object({ error: z.object({
  message: z.string(), filename: z.string().optional(), file_index: z.number().int().nonnegative().optional(),
}) })

export async function submitJobs(files: File[], signal: AbortSignal) {
  const form = new FormData()
  for (const image of files) form.append('images', image)
  const response = await fetch('/api/jobs', { method: 'POST', body: form, signal })
  if ([400, 413, 503].includes(response.status)) {
    const parsed = rejection.safeParse(await response.json().catch(() => null))
    if (!parsed.success) return { error: 'The service rejected the batch. Your selection is still available.' }
    const { message, filename, file_index } = parsed.data.error
    const name = filename ?? (file_index === undefined ? undefined : files[file_index]?.name)
    return { error: name ? `${name} — ${message}` : message }
  }
  if (response.status !== 202) throw new Error('Submission outcome unknown')
  const { jobs } = snapshot.parse(await response.json())
  if (jobs.length !== files.length || jobs.some((item, i) => item.status !== 'queued' || item.filename !== files[i].name)) {
    throw new Error('Invalid submission acknowledgement')
  }
  return { jobs }
}
