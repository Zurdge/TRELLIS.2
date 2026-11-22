# Frontend generation settings

Extend the existing React upload form to let users select generation and GLB export settings before they queue images. The batch form and all API values follow the [generation settings contract](04-generation-settings.md).

## Upload form

- Add a Texture size selector with 1024 and 2048 and a Target triangle count numeric input with the API's integer bounds. Explain that the atlas controls baked material detail and that a mesh can export with fewer triangles than the target.
- Add a collapsed Advanced settings section with Seed and Sampling steps. Explain that one step count applies to coarse structure, geometry and materials. Show the bounds and defaults. Higher counts take longer and can change the result. Add a Reset settings control that restores all four defaults without changing selected files.
- Say that the settings apply to every image in this upload. Use associated labels and field-level errors. Block submission if a field is empty, fractional or outside the supported range. Keep an empty input editable and validate before creating the payload.
- Snapshot the values at submit time, disable the picker and settings controls while the request is in flight, and preserve files and settings on rejection or an uncertain outcome. Keep the chosen settings after successful submission for the next batch and clear the accepted files as the form does today.

## API client and job display

- Extend the Zod job schema with the resolved settings and derive TypeScript types for the form and response. Use `form.append('settings', JSON.stringify(snapshot))` to send a text part alongside the ordered images. Let the browser set the multipart boundary. The Express proxy continues to stream this request on its existing route.
- Keep existing acknowledgement checks and verify that the settings echoed for each accepted job match the submitted snapshot. Show API validation errors through the existing upload feedback. Treat a malformed or mismatched 202 as an uncertain outcome. Keep the files and settings, refresh history, and preserve the existing submission gate until a history refresh succeeds.
- Show a compact settings summary for running, succeeded and failed jobs, with access to the exact saved values. Under the existing waiting count, add an expandable list of queued filenames and their settings in FIFO order. The history API returns newest submissions first, so display queued jobs in dispatch order. Use the values returned for each job so edits for a new batch cannot change what older jobs show. Support history created before this release through the server's legacy defaults. Deploy the API before or together with the updated frontend.

## Verification

- Verify default and custom submissions through Express with several images, including seed 0, both atlas sizes and the numeric endpoints. Queue one batch with custom values, change the form, and queue a second batch. Confirm the settings shown for each job match the persisted values before dispatch, through polling and after page reload.
- Verify Reset settings, keyboard access, validation and rejected requests while files are selected. Verify successful acknowledgements clear only the files and that the next upload starts with the retained settings.
- Run the frontend build and exercise the upload, job progress and GLB download flow against both existing and new records.

[Upload form](../frontend/src/components/UploadForm.tsx) · [API client](../frontend/src/api.ts) · [App and submission state](../frontend/src/App.tsx)
