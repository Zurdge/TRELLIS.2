import { pipelineLabels, type Job } from '../api'

export function JobSettings({ job }: { job: Job }) {
  const { settings, actual_resolution } = job
  return <dl className="muted mt-1 flex flex-wrap gap-x-3 gap-y-1">
    <div><dt className="inline">Requested </dt><dd className="inline">{pipelineLabels[settings.pipeline_type]}</dd></div>
    {actual_resolution !== null &&
      <div><dt className="inline">Generated </dt><dd className="inline">{actual_resolution}³</dd></div>}
    <div><dt className="inline">Texture </dt><dd className="inline">{settings.texture_size} px</dd></div>
    <div><dt className="inline">Triangle target </dt><dd className="inline">{settings.decimation_target.toLocaleString('en-US')}</dd></div>
    <div><dt className="inline">Steps </dt><dd className="inline">{settings.sampling_steps}</dd></div>
    <div><dt className="inline">Seed </dt><dd className="inline">{settings.seed}</dd></div>
  </dl>
}
