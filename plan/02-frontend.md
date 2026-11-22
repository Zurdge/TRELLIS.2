# Frontend service

Build a single-page React application for selecting images, tracking the one job currently on the GPU, and downloading completed GLB assets. The frontend uses the generation API as its source of truth for readiness, queue state, and history. Each image creates a separate GLB. Users can submit a batch while a job is running, and the backend processes the queued jobs one at a time. [Generation API and worker](01-generation-service.md#api-and-worker)

## Libraries and setup

| Library | Role |
| --- | --- |
| React and TypeScript | Render the upload form, current generation, and finished assets. Use component state for selected files, API snapshots, and feedback. |
| Vite | Run the browser development server with hot reload and build the production assets for Express. |
| Express 5 on Node 22 | Serve the Vite build and stream same-origin API requests and downloads to and from the generation container. |
| Tailwind CSS 4 | Use the Vite plugin to compile utility classes for the responsive layout, status labels, and form controls. |
| Zod 4 | Validate the browser’s selected files and the health and job API responses, and infer the corresponding TypeScript types. The Python API remains responsible for decoding and validating uploaded images. |

Use a Node 22 release supported by Vite. Keep the dependency manifest, lockfile, and build configuration in the future `frontend/` project. Use a two-stage Docker build to compile the browser assets and copy them into the Express runtime image. One `docker compose up --build` command will start both containers. The frontend publishes `127.0.0.1:8080`, while the generation service remains on the Compose network. [Vite setup](https://vite.dev/guide/), [Express 5 API](https://expressjs.com/en/5x/api/), [Tailwind Vite setup](https://tailwindcss.com/docs/installation/using-vite), [Zod setup](https://zod.dev/)

## Page layout

1. **Upload images** comes first. Show generator readiness beside the upload area. Use a labeled multi-file picker for 1–8 static JPEG, PNG, or WebP images, up to 10 MiB each. List the selected filenames and explain that each image creates its own GLB. The server also checks the 16-megapixel decoded limit. Enable Generate when the selection is valid, the API is ready, and no submission is in flight.
2. **Currently generating** shows a single entry for the job whose status is `running`. Show the filename, latest stage, and an indeterminate progress indicator. When there is no running job, show an idle state. Show the number of queued jobs as adjacent text. Report individual job failures compactly so the user can tell which image needs another try.
3. **Finished assets** lists succeeded jobs from server history. Show the source filename and a per-asset **Download GLB** link. Use a text empty state until the first asset finishes. Keep history and download links visible while the GPU worker restarts if the API can still serve the files.

Use semantic headings and labels. Keep the page usable with a keyboard and at narrow widths. Pair status colors with text and announce meaningful changes through a polite live region. Keep row keys stable during polling so a focused download link stays in place.

## Data and request flow

- **Health.** Request `/api/health` immediately and every five seconds, with one request in flight. Show **Starting** during the initial check or an explicit startup response, green **Ready** only on confirmed readiness, and **Unavailable** on a failed or blocked check. Ready includes a busy worker that can accept more jobs.
- **Submit.** Append each file to a repeated `images` multipart field and let the browser set the multipart boundary. On a validated `202`, add the returned jobs and clear the selection. On a known rejection, keep the selection and show the error. If the reply is lost or invalid, refresh history, retain the selection, and identify the outcome as unconfirmed. A manual retry may create duplicate jobs if the first request reached the API.
- **Refresh.** Load jobs on page open and poll at about two seconds during active work, slowing to five seconds when idle. Accept only the latest valid snapshot. Keep the last readable snapshot visible when a poll fails and resume polling automatically. Read stage labels from the agreed set in the API plan. The backend decides which job is running, when an asset is ready, and which files are downloadable.
- **Proxy.** Forward only the generation routes under `/api/`, stripping the prefix. Use `GENERATOR_URL=http://generation:8000` in Compose. Stream uploads and binary downloads, preserve upload boundaries and attachment headers, and enforce the 82 MiB request limit. Return a readable error when the backend is unavailable and resolve the service address on new connections so an API container replacement recovers. Use one upstream attempt per request. The browser follows download links directly, without a full-file JavaScript buffer. [API endpoints and limits](01-generation-service.md#api-and-worker), [Compose networking](https://docs.docker.com/compose/how-tos/networking/)

## Developer handoff and acceptance

Use the [developer handoff](03-frontend-implementation.md) to implement the frontend package. Validate it against a stub generation API before testing the real GPU container.

- Verify loading, readiness, and worker recovery. Confirm that a busy worker still accepts a valid selection and that an outage preserves the selected files.
- Submit an invalid batch and confirm none of its files appear in history. Submit two valid images and observe one running job, a pending count, and two downloadable GLBs after both finish. Simulate one failed generation and verify the successful sibling stays available.
- Reload the browser during generation. Verify that history loads, active stage updates resume, and focus stays on the same link through polling. Simulate an ambiguous submission response and confirm the app refreshes history and keeps the selection.
- Verify an API restart, missing artifact responses, and successful binary downloads through Express. Confirm the Windows GPU run and embedded materials in [the application plan](README.md#implementation-and-verification-order).
