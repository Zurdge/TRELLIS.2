# Generation service and Windows Docker

Expose the reference TRELLIS.2 image-to-3D pipeline as an independent local FastAPI service. Each accepted image creates one job and one textured GLB on success. The service runs one generation at a time, including GLB export. Any HTTP client can submit work, poll progress, and download results using the same contract.

## Windows and Docker

1. Enable Docker Desktop’s WSL 2 Linux backend, update WSL, and use the NVIDIA Windows driver. Verify GPU access and record the model, compute capability, and reported VRAM before selecting a build.

   ```powershell
   docker run --rm --gpus all ubuntu:22.04 nvidia-smi --query-gpu=name,compute_cap,memory.total --format=csv
   ```

   [Docker Desktop GPU support](https://docs.docker.com/desktop/features/gpu/), [NVIDIA CUDA on WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)

2. Use a Linux amd64 CUDA **devel** base on Ubuntu 22.04, Python 3.10, a C++ toolchain, Ninja, and the recursive Eigen submodule. Set `TORCH_CUDA_ARCH_LIST` to the detected compute capability. Install PyTorch first and build the required extensions directly in the Dockerfile. The repository installer checks for a visible GPU before installing dependencies. [Repository installer](../setup.sh), [PyTorch CUDA extension build guidance](https://docs.pytorch.org/docs/2.6/cpp_extension.html#torch.utils.cpp_extension.CUDAExtension)

   | GPU | Initial build |
   | --- | --- |
   | RTX 30/40 series | CUDA 12.4.1, PyTorch 2.6.0 and torchvision 0.21.0 from cu124, FlashAttention 2.7.3. |
   | RTX 50 series | CUDA 12.8.1, PyTorch 2.8.0 and torchvision 0.23.0 from cu128, xFormers 0.0.32.post2 from cu128, and `ATTN_BACKEND=xformers`. |

   Test CUDA arithmetic and dense and sparse attention inside the selected container before claiming GPU support. If detection yields another architecture, verify the CUDA and attention compatibility for that specific card before building. [PyTorch versions](https://pytorch.org/get-started/previous-versions/), [FlashAttention requirements](https://github.com/Dao-AILab/flash-attention/tree/v2.7.3#nvidia-cuda-support), [xFormers cu128 wheels](https://download.pytorch.org/whl/cu128/xformers/)

3. Install CuMesh, FlexGEMM, nvdiffrast, O-Voxel, and the Python dependencies used by inference and export. Use pinned sources for the extensions. Start with CuMesh at `12289e1062f0603f2f0d0771b02e1395d247f26f`, FlexGEMM at `6dd94a859c26ee8246888502eada3dd8ad85532e`, and nvdiffrast at `v0.4.0`. Pin Transformers to `4.57.3` for this checkout’s DINOv3 layer access. The GLB export needs OpenCV and nvdiffrast. The HDRI and video path can be omitted. [Extension revisions](https://github.com/Creepybits/trellis2-blackwell-wsl/blob/main/install.sh), [Hosted dependency pins](https://huggingface.co/spaces/microsoft/TRELLIS.2/blob/main/requirements.txt), [GLB exporter](../o-voxel/o_voxel/postprocess.py)

4. Name the service `generation` and give it one GPU reservation. Bind Uvicorn to `0.0.0.0:8000` inside the container, with the port available on the Compose network by default. Mount named volumes for `/data` and the Hugging Face cache. Pass `HF_TOKEN` at runtime after obtaining access to the gated DINOv3 and RMBG 2.0 weights. Local uploads, history, and downloads require no application login. [Compose GPU support](https://docs.docker.com/compose/how-tos/gpu-support/), [Model configuration](https://huggingface.co/microsoft/TRELLIS.2-4B/blob/main/pipeline.json)

## API and worker

### Connect a client

Clients on the Compose network use `http://generation:8000`. The bundled frontend forwards `/api/` through its Express proxy. A different browser frontend uses its own same-origin proxy to the service. For direct clients on the host, document a local Compose override that publishes `127.0.0.1:8000:8000`. The paths below are relative to the generation service root. Document the multipart request, status codes, response schemas, and examples in FastAPI’s OpenAPI schema at `/openapi.json` with API version `1.0.0`. [FastAPI multipart files](https://fastapi.tiangolo.com/tutorial/request-files/), [FastAPI OpenAPI metadata](https://fastapi.tiangolo.com/tutorial/metadata/)

### HTTP contract

| Endpoint | Response |
| --- | --- |
| `GET /health` | `200` with `{"status":"ready"}` once the store and GPU worker can accept work, including while busy. `503` with `{"status":"starting"}` while initializing or replacing the worker. `503` with `{"status":"unavailable","message":"…"}` for a blocking store or worker error. |
| `POST /jobs` | Accept `multipart/form-data` with repeated file fields named `images`. On durable acceptance, return `202` with `{"jobs":[...]}` in upload order. The response captures all jobs as `queued` at acceptance. A subsequent GET may already show progress. |
| `GET /jobs` | `200` with `{"jobs":[...]}` in reverse acceptance order, including queued, running, and terminal records. Return an empty array only for readable, empty history. |
| `GET /jobs/{id}` | `200` with one job object. Return `404` for an unknown or malformed UUID. |
| `GET /jobs/{id}/download` | `200` with the streamed GLB, `Content-Type: model/gltf-binary`, and `Content-Disposition: attachment`. Use the source basename with a `.glb` extension and a safe filename fallback for the header. Return `409` for a known queued, running, or failed job. Return `404` for an unknown ID or a succeeded job whose artifact is missing. |

Use JSON for status, job responses, and endpoint errors. Set `Cache-Control: no-store` on health, job snapshots, and errors. History, job details, and previously completed GLBs remain readable when the GPU is busy or restarting. Return `503` on those routes while recovering the store or when the store cannot be read.

#### Job record

Every job has these fields, including the nullable fields. Jobs transition from `queued` to `running`, then to `succeeded` or `failed`. A running job may have a null stage before the worker reports its first milestone. Terminal records are immutable.

| Field | Value |
| --- | --- |
| `id` | Server-generated UUID v4 string. Use this ID verbatim in URLs. |
| `filename` | Original filename’s basename for display, with path components removed. Filenames may repeat. |
| `status` | `queued`, `running`, `succeeded`, or `failed`. |
| `stage` | The current milestone listed below, or `null` before the first milestone and while queued or terminal. |
| `created_at`, `updated_at` | RFC 3339 UTC strings ending in `Z`. Set both at acceptance and advance `updated_at` on state or stage changes. |
| `error` | Plain-language message on a failed job, otherwise `null`. |
| `download_url` | Service-root path `/jobs/{id}/download` on success, otherwise `null`. Resolve against the client’s configured API base, including any proxy prefix. |

#### Upload and error behavior

- Accept 1–8 static JPEG, PNG, or WebP files per request. Enforce 10 MiB per file, 16,000,000 decoded pixels per image, and 82 MiB for the whole multipart request. Use 1,048,576 bytes per MiB. Enforce the request cap on streamed bodies as well as declared lengths. Validate the actual format, dimensions, frame count, and complete decode with Pillow before committing any jobs. Apply EXIF orientation and preserve alpha during preprocessing. Store files by UUID.
- For endpoint errors other than health, return `{"error":{"code":"…","message":"…"}}`. For a file-specific validation error, add `file_index` (zero-based multipart order) and `filename` within `error`. Keep FastAPI validation errors on these routes in this same format.

| HTTP status | Error code and condition |
| --- | --- |
| `400` | `invalid_upload` for malformed multipart, unexpected parts, the wrong file count, corrupt or unsupported images, or animated files. |
| `413` | `upload_too_large` for an exceeded request, file, or decoded-pixel limit. |
| `404` | `job_not_found` for an unknown or invalid ID. `artifact_not_found` for a succeeded job whose file is missing. |
| `409` | `job_not_downloadable` for a known queued, running, or failed job. |
| `503` | `service_not_ready` when admission is paused. `store_unavailable` when a requested job snapshot or artifact cannot be resolved because the store is unavailable. |
| `500` | `internal_error` for an unexpected service or persistence error. |

For POST, the documented `400`, `413`, and `503` responses guarantee that the batch was not accepted. A connection loss, an unexpected response, or an invalid `202` leaves the outcome unconfirmed. Each accepted POST gets fresh IDs, including repeat submissions. On an unconfirmed response, clients should refresh history before offering a manual retry because retrying may create duplicate jobs.

### FIFO scheduling and GPU execution

- Run a single Uvicorn process and one child using Python’s `multiprocessing` `spawn` context. The API owns the store, admission, dispatch, and final artifact publication. The child owns CUDA, model loading, and temporary GLB creation. Use a single duplex pipe per child. Send a `run` command with the job ID. The child reports `ready` or `init_error` on startup, then `stage`, `artifact_ready`, or `job_failed` with the job ID during execution. A failure tells the API whether the worker must be replaced. Both processes derive file paths from the job ID. Start HTTP while initialization proceeds. Use one Compose replica and one API process per data volume.
- Serialize batch commits and dispatch with the store lock. Validate uploads before taking that lock. Recheck readiness during commit and append the entire batch atomically. Concurrent batches are ordered by durable commit order. Within a batch, keep multipart order. Dispatch the first committed queued record by persisting `running` before sending its ID. Hold that execution slot through export and terminal commit. At most one job can be `running` at any time.
- Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` before importing Torch in the child. Complete the CUDA and attention probes, load `microsoft/TRELLIS.2-4B`, and set `pipeline.low_vram=True` before `pipeline.cuda()`. The child announces readiness after successful initialization. For each job use `pipeline_type="512"`, `num_samples=1`, and seed 42. Report a readable per-job error if preprocessing finds no usable foreground. [Model offloading and preprocessing](../trellis2/pipelines/trellis2_image_to_3d.py), [Python process communication](https://docs.python.org/3.10/library/multiprocessing.html#pipes-and-queues)
- Pass the mesh and voxel attributes to `o_voxel.postprocess.to_glb` as shown in the example, using the same bounds, `texture_size=1024`, `decimation_target=100000`, and `remesh=False`. Export embedded textures with `extension_webp=False`. The initial 512³ profile needs end-to-end validation on the 12 GB GPU. [Reference export](../example.py)

## Progress, storage, and recovery

Add an optional callback at existing pipeline boundaries. Use these stage values in order and persist updates as the work starts.

| API stage | Browser label |
| --- | --- |
| `preparing_image` | Preparing image |
| `extracting_features` | Extracting image features |
| `generating_structure` | Generating structure |
| `generating_shape` | Generating shape |
| `generating_materials` | Generating materials |
| `decoding_mesh` | Decoding mesh |
| `exporting_glb` | Exporting GLB |

The worker reports the export milestone when GLB conversion begins. The API accepts stage and result messages only from the current worker and for its active job. Persist forward stage changes and ignore duplicates. Sampler step counts and exporter details stay in container logs. Clients can poll `GET /jobs/{id}` to track one generation or `GET /jobs` to display the queue and history. A two-second interval while jobs are active and five seconds while idle is suitable for this local service. Use the current stage for indeterminate progress. [Sampler](../trellis2/pipelines/samplers/flow_euler.py), [Exporter](../o-voxel/o_voxel/postprocess.py)

### Commit and recover files

- Store metadata at `/data/jobs.json` as `{"version":1,"jobs":[...]}` in acceptance order. Keep inputs and results under `/data/jobs/<uuid>/`. The array position determines FIFO order even when timestamps tie or change. Stage submissions on the same volume, then make inputs durable before committing metadata. For each update, write and flush a sibling JSON temporary file, atomically replace the index, and flush its parent directory before acknowledging the commit or dispatching work. Readers use the last committed snapshot.
- Have the worker write `model.partial.glb` for the active job. On successful completion, the API verifies the GLB magic, version 2, and declared length, flushes the file, and atomically renames it to `model.glb`. Flush the artifact directory before persisting `succeeded` and its download path. A corrupt export fails the job. Keep accepted sources and completed artifacts on the volume across restarts.
- On startup, validate the index version, job records, unique IDs, and the single-running-job invariant before serving history or dispatching work. Initialize empty history on a new volume. Preserve unreadable or inconsistent metadata, report unavailable, and require operator recovery. With a readable index, clean up orphaned staged uploads and partial outputs. Recover a `running` job as succeeded if its final GLB is valid. Otherwise mark it failed as interrupted. Resume the remaining queue in acceptance order. If a claimed job’s input is missing, record it as failed before sending work to the GPU.

### Supervise the worker

- For a normal per-job failure, commit `failed` with a readable reason, log diagnostics with the job ID, and continue with the next queued job if the worker remains healthy. For a CUDA OOM, fatal CUDA error, or unexpected worker exit, pause admission and dispatch, stop and reap the old worker, then settle the active job using the same final-file recovery rule. Preserve queued jobs and launch a replacement only after the old worker exits. Discard messages from the old connection during recovery.
- Report `starting` during replacement. If initial or replacement startup fails, report `unavailable` until the operator restarts the service. Keep history readable when the store is healthy. On a store write failure, mark the store unavailable, stop dispatching, and stop the child pending a service restart. On shutdown, stop dispatching, stop and join the child, and leave any unfinished running record for startup recovery. If a live worker stalls, keep its last stage visible until an operator restarts the service. Retrying an interrupted or failed job is a fresh submission.

## Acceptance tests

- Use a stub worker and an HTTP client independently of the React frontend. Submit one image, then two concurrent batches with validation held so the second accepts first. Verify immutable `202` acknowledgements, polling through all job states, FIFO by commit order, contiguous batch members, and at most one running job through export. Verify job state and the GLB attachment against both the direct API and the Express proxy. Confirm that history and finished downloads remain available during worker startup and recovery once the store loads.
- Verify all-or-nothing rejection, file indexes and names in errors, exact byte and pixel limits, and streamed multipart limits. Verify unknown IDs, job conflicts, missing GLBs, and attachment headers. Confirm that an unreadable store returns `503` and is preserved.
- Test restarts before acceptance, after acceptance, during generation, and between final GLB rename and the success record. Drop a response after the batch commit and verify that the original jobs can be found in history. Verify that a CUDA OOM or worker crash fails or recovers the active job, replaces the worker without overlap, and leaves accepted siblings eligible to finish. Verify that replacement startup failure keeps history readable and pauses new work.
- Run CUDA and attention checks inside the Windows container and generate a textured GLB from the bundled example. Record peak VRAM and elapsed time, or the last stage and error if the attempt fails. [Example input](../assets/example_image/T.png)
