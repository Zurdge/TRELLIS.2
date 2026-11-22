import type { FormEvent, RefObject } from 'react'

type Props = {
  files: File[]
  picker: RefObject<HTMLInputElement | null>
  busy: boolean
  enabled: boolean
  validation?: string
  feedback: string
  choose: (files: File[]) => void
  submit: (event: FormEvent<HTMLFormElement>) => void
}

export function UploadForm({ files, picker, busy, enabled, validation, feedback, choose, submit }: Props) {
  return <section className="panel" aria-labelledby="upload-title">
    <h2 id="upload-title">Upload images</h2>
    <p className="muted mt-2" id="upload-limits">Choose up to 8 static JPEG, PNG, or WebP images, with a limit of 10 MiB and 16 MP per image. Each image creates a separate GLB.</p>
    <form onSubmit={submit} className="mt-5 space-y-4">
      <div>
        <label htmlFor="images" className="mb-2 block text-sm font-medium">Image files</label>
        <input ref={picker} id="images" type="file" multiple accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
          aria-describedby="upload-limits" disabled={busy} onChange={event => choose(Array.from(event.target.files ?? []))}
          className="block w-full min-w-0 rounded-lg border border-slate-700 text-sm file:mr-3 file:border-0 file:bg-slate-800 file:px-4 file:py-2.5 file:text-slate-100" />
      </div>
      {files.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
        {files.map((file, index) => <li className="wrap-anywhere" key={index}>{file.name}</li>)}
      </ul>}
      {validation && <p className="text-sm text-rose-300">{validation}</p>}
      <button className="button" disabled={!enabled}>{busy ? 'Submitting…' : 'Generate'}</button>
      <p role="status" className="muted empty:hidden">{feedback}</p>
    </form>
  </section>
}
