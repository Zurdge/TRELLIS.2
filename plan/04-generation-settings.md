# Generation settings for queued uploads

Extend the Docker application's existing upload endpoint so it accepts generation and GLB export settings alongside the images. Each accepted job stores its own resolved settings and the worker consumes that snapshot when the job starts. All images in one upload share the submitted settings. Users can submit separate batches for different settings.

## Initial API contract

`POST /jobs` accepts 1–8 repeated `images` file parts and one optional `settings` text part containing a JSON object. Keep images-only clients working. Omitted keys use the defaults below. Return the fully resolved `settings` object in each job in the 202 acknowledgement, history and detail responses.

| Key | Default | Accepted values for the first release | Effect |
| --- | --- | --- | --- |
| `seed` | 42 | Integer 0–2,147,483,647 | Picks the starting noise. A fixed seed helps compare settings. |
| `sampling_steps` | 12 | Integer 8–24 | Applies the same step count to coarse structure, geometry and material samplers. |
| `texture_size` | 1024 | 1024 or 2048 pixels per side | Controls the GLB texture atlas. Doubling the side produces four times as many texels. |
| `decimation_target` | 100000 | Integer 50,000–500,000 | Sets the target triangle count for simplification. A mesh already below the target can stay below it. |

The existing 512³ mode, one sample, automatic preprocessing and remeshing off remain the first release's worker profile. Guidance strength, guidance rescale and timestep scheduling follow the checkpoint defaults. Step counts control compute and can change fidelity in either direction. The proposed bounds are rollout choices to verify on the local GPU. [Model defaults](https://huggingface.co/microsoft/TRELLIS.2-4B/blob/main/pipeline.json) · [Worker](/C:/Users/James/Documents/Projects/TRELLIS.2/generation/worker.py:75) · [Local validation](/C:/Users/James/Documents/Projects/TRELLIS.2/generation/README.md:64)

## Validation, storage and worker handoff

1. Allow one text field with a 4 KiB part limit while retaining the file and multipart completion checks. Return HTTP 400 with `invalid_upload` for structural failures, including parser limits, duplicate form fields, unexpected fields and settings sent as a file. Keep HTTP 413 for the existing total body limit and preserve upload cleanup. For a valid settings text part, return HTTP 400 with `invalid_settings` for malformed JSON, duplicate JSON keys, unknown keys, a non-object value or invalid values. Name the offending key when known. Strictly validate integer values and reject booleans, numeric strings and null. Describe the JSON text part and its fields, defaults, bounds and example in OpenAPI.
2. Validate settings before accepting the batch and persist the complete resolved values in a frozen settings model on each job. Accept the record and its staged image through the existing store transaction. New requests resolve against the current defaults. Apply the explicit legacy baseline shown above only to persisted records whose settings field is absent. Require complete valid values when that field exists. Keep persisted validation compatible with previously accepted values even if future admission limits change. Keep index version 1 readable by the new service. An older service rejects records containing the new field, so keep a volume backup if rollback to that version is required.
3. Include settings in the supervisor's run command with the job ID and update the worker stubs to match. Pass the step count into all three sampler overrides as `steps`, pass the seed to `pipeline.run`, and pass texture size and decimation target to `to_glb`. Preserve FIFO dispatch, one active job and existing error and worker recovery behavior. [Multipart endpoint](../generation/api.py) · [Job store](../generation/store.py) · [Supervisor](../generation/supervisor.py) · [Worker](../generation/worker.py)

## Hardware validation and later options

- Before exposing the upper bounds to users, run the Docker worker on representative simple and detailed images on the RTX 3060. For each image, fix the seed and test defaults, maximum steps with default export, maximum export settings on the baseline mesh, and the combined maximum. Require each run to finish without an OOM. Capture stage times in the validation harness, peak CUDA allocated/reserved and board usage, actual face count, GLB size and visual results. Verify import and embedded materials in Blender. If a candidate fails or takes unacceptably long on the target, reduce the released range and update the API schema, frontend and docs together.
- Evaluate 1024³ cascade separately. It requires loading the higher-resolution shape and material checkpoints, which the Docker worker currently excludes. Consider 1536³ cascade after 1024³ validation. Before exposing cascade modes, define supported modes per deployment and record requested and actual resolution separately. The 1536³ token budget can lower the actual grid independently of available VRAM. [Model loading](../generation/worker.py) · [Cascade and token budget](../trellis2/pipelines/trellis2_image_to_3d.py)
- Keep DINOv3, background-removal checkpoints, token budget and offloading under deployment control. Revisit advanced guidance or remeshing after fixed-seed comparisons establish a benefit.

## Acceptance checks

- An images-only request returns the baseline. Two consecutive uploads with different settings each preserve their own values through acknowledgement, FIFO dispatch, worker recreation and service restart. Every image in a batch receives the same resolved values. Verify queued jobs resume with stored settings and interrupted active jobs keep those settings through existing recovery.
- Test invalid types, unknown or duplicate keys, duplicate form parts, incomplete bodies and invalid values alongside valid images. Test valid settings padded to 4,096 bytes and rejection at 4,097 bytes. Submit eight images with settings interleaved and verify file errors still use the zero-based image index. Verify zero new jobs and no staged files left behind on rejection.
- Use a stub worker to verify the settings received with each job ID and a GPU smoke run to verify the sampler and GLB exporter receive the values. Verify existing job history, downloads, and failure recovery.
