import { z } from 'zod'

export const stages = {
  loading_checkpoints: 'Loading generation checkpoints',
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
export const settingsLimits = {
  seed: { min: 0, max: 2_147_483_647 },
  sampling_steps: { min: 8, max: 24 },
  decimation_target: { min: 50_000, max: 500_000 },
} as const
export const textureSizes = [1024, 2048] as const
const pipelineTypes = ['512', '1024', '1024_cascade', '1536_cascade'] as const
export const pipelineLabels = {
  '512': '512³',
  '1024': '1024³ direct',
  '1024_cascade': '1024³ cascade',
  '1536_cascade': '1536³ cascade',
} satisfies Record<typeof pipelineTypes[number], string>
const actualResolutions = {
  '512': [512],
  '1024': [1024],
  '1024_cascade': [1024],
  '1536_cascade': [1024, 1152, 1280, 1408, 1536],
}
export const generationSettings = z.object({
  pipeline_type: z.enum(pipelineTypes),
  seed: z.number().int().min(settingsLimits.seed.min).max(settingsLimits.seed.max),
  sampling_steps: z.number().int().min(settingsLimits.sampling_steps.min).max(settingsLimits.sampling_steps.max),
  texture_size: z.literal(textureSizes),
  decimation_target: z.number().int().min(settingsLimits.decimation_target.min).max(settingsLimits.decimation_target.max),
})
export type GenerationSettings = z.infer<typeof generationSettings>
export const defaultSettings = {
  pipeline_type: '512',
  seed: 42,
  sampling_steps: 12,
  texture_size: 1024,
  decimation_target: 100_000,
} satisfies GenerationSettings

export type SettingsInput = Record<keyof GenerationSettings, string>
export const defaultSettingsInput: SettingsInput = {
  pipeline_type: defaultSettings.pipeline_type,
  seed: String(defaultSettings.seed),
  sampling_steps: String(defaultSettings.sampling_steps),
  texture_size: String(defaultSettings.texture_size),
  decimation_target: String(defaultSettings.decimation_target),
}

const base = z.object({
  id: z.uuidv4(),
  filename: z.string().min(1),
  created_at: z.iso.datetime(),
  updated_at: z.iso.datetime(),
  stage: z.null(),
  download_url: z.null(),
  error: z.null(),
  settings: generationSettings,
  actual_resolution: z.null(),
})
const job = z.discriminatedUnion('status', [
  base.extend({ status: z.literal('queued') }),
  base.extend({ status: z.literal('running'), stage: z.enum(Object.keys(stages) as (keyof typeof stages)[]).nullable() }),
  base.extend({ status: z.literal('failed'), error: z.string().min(1) }),
  base.extend({ status: z.literal('succeeded'), download_url: z.string(), actual_resolution: z.number().int().nullable() }),
]).refine(value => value.status !== 'succeeded' || (
  value.download_url === `/jobs/${value.id}/download` &&
  (value.actual_resolution === null || actualResolutions[value.settings.pipeline_type].includes(value.actual_resolution))
))

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

export async function submitJobs(files: File[], settings: GenerationSettings, signal: AbortSignal) {
  const form = new FormData()
  for (const image of files) form.append('images', image)
  form.append('settings', JSON.stringify(settings))
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
  const settingKeys = Object.keys(defaultSettings) as (keyof GenerationSettings)[]
  if (jobs.length !== files.length || jobs.some((item, i) =>
    item.status !== 'queued' || item.filename !== files[i].name ||
    settingKeys.some(key => item.settings[key] !== settings[key])
  )) {
    throw new Error('Invalid submission acknowledgement')
  }
  return { jobs }
}
