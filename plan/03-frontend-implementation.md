# Frontend implementation handoff

Build a single local page for submitting image-to-3D jobs and retrieving GLB assets. React owns the browser UI, Tailwind CSS styles it, Zod validates inputs and API responses, and Express serves the page and forwards requests to the planned generation service. Keep the job queue and persistence in the generation service. [Frontend overview](02-frontend.md), [Generation API contract](01-generation-service.md#api-and-worker)

## 1. Package and file setup

- Use Node 22.12 or newer in the Node 22 line, npm, React and React DOM 19, Express 5, Tailwind CSS 4 with `@tailwindcss/vite`, and Zod 4. Use Vite 7 with its React plugin and TypeScript 5.9 in strict mode. Record compatible versions in `package.json` and check in the npm lockfile when materializing the app. Use `npm ci` for repeatable builds. [Vite 7 requirements](https://v7.vite.dev/guide/), [React client API](https://react.dev/reference/react-dom/client/createRoot), [Tailwind’s Vite setup](https://tailwindcss.com/docs/installation/using-vite), [Express installation](https://expressjs.com/en/starter/installing/), [Zod 4](https://zod.dev/)
- Use Vite to build a static client into `dist/`. Have the package’s build script run TypeScript checking before the Vite build. The start script launches Express. In development, run Express on port 8080 and configure Vite to forward `/api/` to Express with the path intact. Production uses Express for both the built client and the API proxy.
- Set `GENERATOR_URL` to `http://127.0.0.1:8000` by default and `http://generation:8000` in Compose. Default `PORT` to 8080 and `HOST` to `127.0.0.1`. In the container, set `HOST=0.0.0.0` and publish only `127.0.0.1:8080:8080` on the host.

| Future path | Responsibility |
| --- | --- |
| `frontend/package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts` | Dependencies, scripts, client type checking, and Vite plugins and development proxy. |
| `frontend/index.html`, `src/main.tsx`, `src/styles.css` | HTML entry, React root, and Tailwind import and shared visual styles. |
| `frontend/src/App.tsx` | Page layout, submission flow, and job data selection. |
| `frontend/src/components/` | `ServiceHealth`, `UploadForm`, `CurrentGeneration`, and `FinishedAssets`. |
| `frontend/src/api.ts`, `src/usePolling.ts` | Zod schemas, typed API calls, and a polling hook with cancellation and immediate refresh. |
| `frontend/server.mjs`, `Dockerfile`, `.dockerignore` | Express, streaming proxy, and frontend image. |
| Repository Compose configuration | Runs the frontend and generation service on the same network. |

## 2. Express and Compose

Mount the proxy before serving static files. Match the method and path explicitly, strip `/api`, and send the mapped path to the configured upstream. Validate job IDs as UUID path segments. Return a JSON `404` for unknown API paths or invalid IDs and `405` with an `Allow` header for unsupported methods on known paths.

| Browser request | Forward to generation service |
| --- | --- |
| `GET /api/health` | `GET /health` |
| `GET /api/jobs` | `GET /jobs` |
| `POST /api/jobs` | `POST /jobs` |
| `GET /api/jobs/{id}` | `GET /jobs/{id}` |
| `GET /api/jobs/{id}/download` | `GET /jobs/{id}/download` |

- Use a fixed HTTP upstream origin from `GENERATOR_URL`. Forward upload bodies as streams with backpressure. Preserve the multipart `Content-Type` boundary and any valid `Content-Length`. Skip Express body parsing on proxy routes. Reject a declared request size over 82 MiB before opening the upstream connection and count streamed bytes for requests without a declared length. On overflow, abort the upstream request and return `413`. The generation service validates the batch atomically and remains authoritative for individual file sizes, pixel limits, and contents.
- Stream upstream responses and preserve the actual status. Forward `Content-Type`, `Content-Length` when present, and `Content-Disposition`. On downloads, forward the request’s `Range` header and the upstream’s `Accept-Ranges` and `Content-Range` headers when present. Exclude hop-by-hop headers. Keep status and health responses uncached.
- Open a fresh upstream connection using the Compose service hostname so a new generation container can receive subsequent requests after replacement. Forward a request once. Cancel the upstream operation when the downstream disconnects. Send `502` when the upstream cannot be reached and `504` on timeout before response headers. If a stream fails after headers, close the incomplete response. Apply a response-start timeout of 5 seconds for reads and 120 seconds for multipart submissions. Use a 120-second inactivity timeout after download headers so transfers that continue making progress can finish. [Compose networking](https://docs.docker.com/compose/how-tos/networking/), [Node HTTP API](https://nodejs.org/api/http.html)
- Serve Vite’s `dist/` and its entry page from Express. The frontend Dockerfile should build with a Node 22 build stage, the lockfile, and `npm ci`. Use a Node 22 runtime stage containing the built assets, the server, and production dependencies installed from the same lockfile. The Compose frontend service starts independently of GPU readiness. The browser should be able to open the page even if the generator is still starting or its container is absent.

## 3. Validate the browser boundary

- Define a Zod health schema with the statuses `starting`, `ready`, and `unavailable`, requiring a message for `unavailable`. A valid `200` means ready only if its body says `ready`. A valid `503` body may report `starting` or `unavailable`. Treat network failures, other response statuses, and invalid JSON or schema data as unavailable.
- Define a discriminated Zod job union that follows the generation contract. Require a UUID, filename, creation and update timestamps, status, and the three nullable fields `stage`, `error`, and `download_url`. Queued and terminal jobs have a null stage. Running jobs report a documented stage or a null stage while entering the worker. Failed jobs include an error and have a null download URL. A successful job’s URL must equal `/jobs/{id}/download` for its own UUID. Derive the TypeScript job types from the schemas. [Documented stages](01-generation-service.md#progress-storage-and-recovery)
- Parse `GET /jobs` as a job array inside the `jobs` envelope and reject duplicate IDs or a snapshot with more than one running job. Parse a `202` submission as the same envelope with one distinct `queued` record per selected image, in upload order. Use subsequent GETs for live status. Parse the API error envelope for upload rejections and display `error.message` with the offending file when `file_index` or `filename` is present. [Generation API responses](01-generation-service.md#http-contract)
- Validate 1–8 non-empty selected files, at most 10 MiB each. Accept PNG, JPEG, or WebP MIME types, with filename extension as a fallback when MIME is missing or generic. Show the 16-megapixel decoded pixel limit in the UI and leave content, pixel count, and still-image validation to the API. Create a `FormData` with one `images` field per file in selection order and let the browser set the multipart boundary.

## 4. Build the page

Use one centered, responsive column. Put the health badge in the header. Use text labels for status and give the file picker, Generate button, and download links a visible keyboard focus treatment. Update the polite live region only when readiness, progress, or completion changes.

```text
TRELLIS.2                                       Ready

Upload images
  [Choose files]   Up to 8 images · 10 MiB and 16 MP per image
  Each image creates a separate GLB.
  Selected filenames
  [Generate]
  Submission result or error

Currently generating
  figurine.png
  Generating materials
  [indeterminate progress]     2 waiting

Finished assets
  sculpture.png   Completed …                  [Download GLB]
  bust.png        Completed …                  [Download GLB]
  Failed generations (collapsed when present)
```

- **UploadForm.** Use a labeled native multiple-file input and list selected filenames. Disable Generate while the selection is invalid, the service is starting or unavailable, or submission is unresolved. A busy worker still accepts jobs if health is ready. Lock the selection during the request to keep the submitted batch and the visible state consistent. On a valid `202`, merge the returned jobs into local state immediately, clear the picker, then refresh the jobs list. For a definitive rejection, preserve the selection and show the API’s file-specific message when available.
- **CurrentGeneration.** Find the sole job with status `running` and render one entry with its filename and latest named stage. Use “Starting generation” for a null running stage and “No generation running” when there is no active job. Show the number of queued jobs as a count, even when no job is running. Use an indeterminate progress element and display real stages without estimating percentages or time remaining.
- **FinishedAssets.** Include only jobs with status `succeeded` and a validated download URL. Sort by most recent completion update, keep rows keyed by UUID, and include source filename, completion time, and `Download GLB`. Use an anchor to `/api` plus the validated API-relative download path. The proxy preserves the upstream attachment filename so the browser can save the file. Keep links available during a GPU worker reload. Show an empty state when there are no completed jobs and a compact disclosure for failed jobs and errors when present.

## 5. Coordinate requests and updates

- Start independent health and job reads on page load. Poll health every five seconds and jobs every two seconds while any job is queued or running. Poll jobs every five seconds when idle so work submitted in another tab also appears. Schedule the next poll after the current one settles, cancel timers and requests on unmount, and allow an immediate refresh to replace the pending poll. Use a browser read timeout slightly longer than the proxy timeout.
- Abort or invalidate the in-flight jobs request before merging a valid submission acknowledgement. Add acknowledged IDs that are absent from the current snapshot and preserve more recent records already observed for those IDs. A GET started before the acknowledgement may finish late, so allow only responses from the latest accepted request to update the snapshot. Keep acknowledged rows visible until a later snapshot includes their IDs. As soon as a job appears in a GET response, use that snapshot as its source of truth.
- If the upload returns the documented rejection statuses `400`, `413`, or `503`, show the error and preserve the selection. A lost connection, unexpected response, or invalid `202` body leaves the outcome uncertain because the API may have committed the batch. Preserve the selection and refresh history before offering a manual retry. Identify the submission as unconfirmed and ask the user to check history for matching files to avoid duplicates.
- Keep the last valid job snapshot when polling fails, show a brief stale-data message, and keep polling. Disable new submissions when health becomes unavailable. After a reload, recover active, queued, completed, and failed jobs from the API’s persisted history.

## 6. Acceptance before integration

| Scenario | Expected outcome |
| --- | --- |
| Start Express before the generation backend | Page loads with submission disabled. Once health reports ready, a valid selection can be submitted. |
| Submit one valid image, then several valid images while busy | One job per file is acknowledged in upload order. Only one running entry appears and the waiting count updates. |
| Submit an invalid image or exceed a limit | The batch is rejected with `400` or `413`, no records are created, and the selection stays available for correction. Verify the request cap for uploads with and without a declared length. |
| Delay a list response while submitting and simulate a lost submission response | A stale GET does not erase acknowledged jobs. A lost response preserves selection and refreshes history without repeating the POST. |
| Stop or recreate the API container during work | The UI retains its last snapshot and shows lost connectivity. Later polls reconnect after replacement and reflect recovered jobs. |
| Finish one job and fail another | The finished job downloads with the expected bytes, `model/gltf-binary`, and attachment filename. The failure reason remains visible through the failure disclosure. |
| Poll while using only the keyboard | Focus stays on the existing control or download link. Status and stage changes are understandable through text and assistive technology. |

Use a stub upstream to cover the browser and proxy cases independently of CUDA. Run the Windows end-to-end acceptance after the generator is implemented. [Windows verification order](README.md#implementation-and-verification-order)
