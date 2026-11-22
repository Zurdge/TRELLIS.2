# Generation resolution in the upload form

Add a **Generation resolution** selector to the existing batch settings. Use the exact API values in the [service plan](06-generation-resolution-service.md).

| Label | Submitted value |
| --- | --- |
| 512³ | `"512"` |
| 1024³ direct | `"1024"` |
| 1024³ cascade | `"1024_cascade"` |
| 1536³ cascade | `"1536_cascade"` |

## Upload behavior

- Put the selector alongside the existing texture size and triangle target controls. Label it as the generation grid. Explain that cascade modes refine a 512³ shape, while higher modes can take longer and require more memory. Mention that the first higher-resolution job may need to download checkpoints and that the 1536³ cascade can finish at a lower grid for complex shapes.
- Offer all four options. Use 512³ on initial load and when resetting settings. The selected value applies to every image in the upload. Retain it for the next batch after a successful submission and preserve it with selected files on rejection or an uncertain acknowledgement. Disable the selector while the request is in flight.
- Validate the four values in Zod and send the selected string as `settings.pipeline_type` in the existing multipart JSON text part. Verify that every 202 job echoes the selected mode, alongside the other submitted settings. Keep the existing recovery flow for a missing or mismatched acknowledgement. Update the API before or together with the frontend. [Frontend API client](../frontend/src/api.ts) · [Upload form](../frontend/src/components/UploadForm.tsx) · [Form state](../frontend/src/App.tsx)

## Job display

- Show the requested mode in the shared settings summary for queued, running, finished, and failed jobs. Use the value from each job record, so changing the form leaves existing jobs' labels intact.
- Validate the nullable `actual_resolution` in job responses. For a completed job with a measured result, display its generated grid. If it differs from the request, show both, for example **Requested 1536³ cascade · Generated 1280³**. For older records with null, show just the saved mode. Surface checkpoint and out-of-memory messages in the existing failed generations list. [Shared settings display](../frontend/src/components/JobSettings.tsx) · [Finished assets](../frontend/src/components/FinishedAssets.tsx)
- Recognize the new `loading_checkpoints` stage in the API schema and show **Loading generation checkpoints** in progress and status announcements.

## Verification

- Through Express, submit all four modes against a stub worker. Verify exact string values, one requested mode per batch, correct acknowledgement and queue order, and unchanged settings after reload. Test reset, keyboard use, rejection, and mismatched acknowledgements with chosen settings and files preserved as appropriate.
- Verify that a reduced 1536³ result shows both grids, an older successful job can show only the requested mode, and failed jobs show the selected mode and server error. Run the production frontend build.
