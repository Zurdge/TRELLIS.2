# Browser frontend

Provide one page for a local user to check generator readiness, upload one or more images, see each generation’s progress, and download completed GLBs.

## Delivery

Build static HTML, CSS, and vanilla JavaScript in `frontend/`. Use a small Nginx container to serve the page at `http://localhost:8080` with a loopback host binding. Start both projects with `docker compose up --build`. Start serving the frontend while the API initializes.

Forward the browser’s same-origin `/api/` requests to the Compose generation service on port `8000` and strip the prefix. For example, the browser’s `/api/health` reaches the API’s `/health`. Configure Nginx to resolve the service name again after container replacement, honor the 82 MiB multipart limit, and disable upstream retries for upload requests. [API contract](01-generation-service.md#api-and-worker), [Compose networking](https://docs.docker.com/compose/how-tos/networking/)

## Interface and behavior

- **Service status.** Check health on page load and every five seconds. Display **Starting** during initial connection, model loading, or worker recovery. Show green **Ready** only after the API confirms readiness. Show **Unavailable** if health fails or the API reports a blocking error. Keep existing selections and completed history visible during an outage.
- **Upload.** Use a labeled native multi-file input. Show the accepted formats and limits, list the selected filenames, and explain that each image creates a separate GLB. Enable Generate only when the service is ready and the selection passes client checks. Prevent duplicate clicks during submission. Clear the selection after a successful `202` and show the new jobs immediately. If validation fails, identify the affected file when available and retain the selection for correction. If the upload response is lost, refresh history, retain the selection, and explain that submission could not be confirmed before offering a manual retry.
- **History and progress.** Load the server’s job history on page load and poll it every two seconds while jobs are active. Resume polling after a failed check and show that updates are temporarily unavailable while keeping last-known rows visible. Display each image’s filename, job state, and actual pipeline stage. Use an indeterminate progress indicator for running jobs. Update rows by UUID to preserve focus. Pair colors with text labels and use a polite live region for meaningful transitions.
- **Downloads and failures.** Show a direct download link for each successful job by adding `/api` to its API-relative download path. The browser downloads from the service without buffering the whole file in JavaScript. Keep completed downloads usable while the GPU worker reloads. Display the recorded reason on failed jobs.

## Acceptance tests

- Verify that upload is disabled during startup and outages, enabled when ready, and still available when the worker is busy. Verify keyboard operation and focus retention during polling.
- Submit one image, then two images. Confirm one job per image and a separate download for each success. Verify that an invalid file rejects the whole submission and that a generation failure leaves its sibling’s download available.
- Reload during generation, interrupt polling, and recreate the API container. Confirm that the history reloads, polling resumes, the proxy reconnects, and finished downloads work. Verify the complete Windows flow with the actual GPU and embedded materials as described in the [application plan](README.md#implementation-and-verification-order).
