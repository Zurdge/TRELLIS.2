import type { FormEvent, RefObject } from 'react'
import { defaultSettings, pipelineLabels, settingsLimits, textureSizes, type SettingsInput } from '../api'

type Props = {
  files: File[]
  picker: RefObject<HTMLInputElement | null>
  busy: boolean
  enabled: boolean
  validation?: string
  feedback: string
  settings: SettingsInput
  invalidSettings: PropertyKey[]
  changeSetting: (key: keyof SettingsInput, value: string) => void
  resetSettings: () => void
  choose: (files: File[]) => void
  submit: (event: FormEvent<HTMLFormElement>) => void
}

const inputClass = 'block w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm'
const labels = {
  seed: 'Seed',
  sampling_steps: 'Sampling steps',
  decimation_target: 'Target triangle count',
}

export function UploadForm({ files, picker, busy, enabled, validation, feedback, settings, invalidSettings,
  changeSetting, resetSettings, choose, submit }: Props) {
  function numberField(key: keyof typeof labels) {
    const { min, max } = settingsLimits[key]
    const invalid = invalidSettings.includes(key)
    return <div>
      <label htmlFor={key} className="mb-2 block text-sm font-medium">{labels[key]}</label>
      <input id={key} type="number" min={min} max={max} step={1} required value={settings[key]}
        onChange={event => changeSetting(key, event.target.value)} className={inputClass}
        aria-invalid={invalid} aria-describedby={`${key}-range`} />
      <p id={`${key}-range`} className={invalid ? 'mt-1 text-sm text-rose-300' : 'muted mt-1'}>
        {invalid ? 'Enter a whole number from ' : ''}{min.toLocaleString('en-US')}–{max.toLocaleString('en-US')}.
        {' '}Default {defaultSettings[key].toLocaleString('en-US')}.
      </p>
    </div>
  }

  return <section className="panel" aria-labelledby="upload-title">
    <h2 id="upload-title">Upload images</h2>
    <p className="muted mt-2" id="upload-limits">Choose up to 8 static JPEG, PNG, or WebP images, with a limit of 10 MiB and 16 MP per image. Each image creates a separate GLB.</p>
    <form onSubmit={submit} noValidate className="mt-5 space-y-4">
      <div>
        <label htmlFor="images" className="mb-2 block text-sm font-medium">Image files</label>
        <input ref={picker} id="images" type="file" multiple accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
          aria-describedby="upload-limits" disabled={busy} onChange={event => choose(Array.from(event.target.files ?? []))}
          className="block w-full min-w-0 rounded-lg border border-slate-700 text-sm file:mr-3 file:border-0 file:bg-slate-800 file:px-4 file:py-2.5 file:text-slate-100" />
      </div>
      {files.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
        {files.map((file, index) => <li className="wrap-anywhere" key={index}>{file.name}</li>)}
      </ul>}
      <fieldset disabled={busy} className="space-y-3 border-t border-slate-800 pt-4">
        <legend className="sr-only">Generation settings</legend>
        <p className="muted">These settings apply to every image in this upload.</p>
        <div>
          <label htmlFor="pipeline-type" className="mb-2 block text-sm font-medium">Generation resolution</label>
          <select id="pipeline-type" value={settings.pipeline_type} aria-describedby="pipeline-help"
            onChange={event => changeSetting('pipeline_type', event.target.value)} className={inputClass}>
            {Object.entries(pipelineLabels).map(([mode, label]) => <option key={mode} value={mode}>{label}</option>)}
          </select>
          <p id="pipeline-help" className="muted mt-1">
            Cascade modes refine a 512³ shape. Higher grids can take longer and require more memory.
            The first higher-resolution job may download checkpoints. Complex shapes can bring the 1536³ cascade down to a smaller grid.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="texture-size" className="mb-2 block text-sm font-medium">Texture size</label>
            <select id="texture-size" value={settings.texture_size} aria-describedby="texture-help"
              onChange={event => changeSetting('texture_size', event.target.value)} className={inputClass}>
              {textureSizes.map(size => <option key={size} value={size}>{size} × {size} px</option>)}
            </select>
            <p id="texture-help" className="muted mt-1">Controls baked material detail. Default {defaultSettings.texture_size} px.</p>
          </div>
          {numberField('decimation_target')}
        </div>
        <p className="muted">A mesh can export with fewer triangles than the target.</p>
        <details>
          <summary className="cursor-pointer text-sm font-medium">
            Advanced settings
            {(invalidSettings.includes('seed') || invalidSettings.includes('sampling_steps')) &&
              <span className="ml-2 text-rose-300">Check the highlighted fields</span>}
          </summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {numberField('seed')}
            {numberField('sampling_steps')}
          </div>
          <p className="muted mt-2">The same step count applies to coarse structure, geometry and materials. Higher counts take longer and can change the result.</p>
        </details>
        <button type="button" className="text-sm font-medium text-teal-200 underline disabled:opacity-50" onClick={resetSettings}>Reset settings</button>
      </fieldset>
      {validation && <p className="text-sm text-rose-300">{validation}</p>}
      <button className="button" disabled={!enabled}>{busy ? 'Submitting…' : 'Generate'}</button>
      <p role="status" className="muted empty:hidden">{feedback}</p>
    </form>
  </section>
}
