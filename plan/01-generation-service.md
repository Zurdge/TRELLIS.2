# Generation service and Windows Docker

Expose the reference TRELLIS.2 image-to-3D pipeline as a local FastAPI service. Accept a batch of image files, create a UUID for each, and generate one textured GLB per image.

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

4. Give only the generation service a GPU reservation. Keep its port `8000` on the Compose network. Mount named volumes for `/data` and the Hugging Face cache. Pass a Hugging Face token at runtime after obtaining access to the gated DINOv3 and RMBG 2.0 weights. Make the frontend available while the weights are loading. Local uploads, history, and downloads require no application login. [Compose GPU support](https://docs.docker.com/compose/how-tos/gpu-support/), [Model configuration](https://huggingface.co/microsoft/TRELLIS.2-4B/blob/main/pipeline.json)

## API and worker

Run one Uvicorn process and one spawned GPU worker in the same generation container. The API owns the queue and writes job records. The worker loads the model, processes one job at a time, and sends readiness, stage, and result messages back to the API. Keep the HTTP listener responsive during model initialization and CUDA work.

| Endpoint | Response |
| --- | --- |
| `GET /health` | `200` with `{"status":"ready"}` when the worker can accept jobs, including while busy. `503` with status `starting` or `unavailable` during initialization, recovery, or a blocking error. A blocking error includes a short message. |
| `POST /jobs` | Multipart request with repeated `images` fields. Validate the entire batch and persist its queued records before replying `202` with `{"jobs":[...]}` in upload order. |
| `GET /jobs` | `200` with `{"jobs":[...]}` newest first, including jobs loaded from disk. |
| `GET /jobs/{id}` | `200` with the current job, or `404` for an unknown ID. |
| `GET /jobs/{id}/download` | Stream a successful result as `model/gltf-binary` with an attachment filename based on the source image. Return `409` for pending or failed jobs, and `404` for an unknown ID or missing artifact. |

- A job has `id` (UUID), `filename`, `status`, `stage`, `created_at`, `updated_at`, `error`, and `download_url`. Timestamps are UTC. Statuses are `queued`, `running`, `succeeded`, and `failed`. The stage is null while queued or terminal, the error is null unless failed, and the download URL is null unless succeeded. Use `/jobs/{id}/download` as the API-relative download path.
- Accept 1–8 static PNG, JPEG, or WebP files, with limits of 10 MiB and 16 megapixels per file and 82 MiB per multipart request. Decode with Pillow to validate format and contents. Apply EXIF orientation and preserve alpha for inference. Use the UUID for storage paths. Return `400` for invalid counts or images, `413` for size limits, and `503` when the generation service cannot accept jobs. Include the offending filename when an error applies to a specific file.
- Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` before importing Torch. Load `microsoft/TRELLIS.2-4B`, enable low-VRAM offloading, and use `pipeline_type="512"`, `num_samples=1`, and seed 42. Pass the mesh and voxel attributes to `o_voxel.postprocess.to_glb` as shown in the repo example, using the same bounds, `texture_size=1024`, `decimation_target=100000`, and `remesh=False`. Export embedded textures with `extension_webp=False`. [Model offloading](../trellis2/pipelines/trellis2_image_to_3d.py), [Reference export](../example.py)

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

The worker reports the export milestone around the GLB exporter. The existing sampler step counts and exporter substage messages belong in the container logs. The browser will show the current stage and an indeterminate indicator because total job duration varies with the input and GPU. [Sampler](../trellis2/pipelines/samplers/flow_euler.py), [Exporter](../o-voxel/o_voxel/postprocess.py)

- Keep metadata in `/data/jobs.json` and job files under `/data/jobs/<uuid>/`. Stage and validate all input files on the same volume, then commit the complete batch with one locked atomic JSON replacement before acknowledging it. Process committed queued jobs in insertion order. Clean up inputs from uncommitted submissions.
- Export to a temporary file, verify the GLB header and declared file size, then atomically rename it to `model.glb` before persisting success. Flush file data and directory changes supporting the upload and export commits. On startup, resume queued jobs in order, recover a running job with a valid committed GLB as succeeded, and mark other interrupted jobs failed. Remove partial artifacts and preserve an unreadable job store while reporting the problem through readiness.
- Report readable errors for individual jobs and log diagnostic details with the job ID. If the CUDA worker fails or runs out of memory, persist the active job’s outcome, reload the model in a replacement worker, and resume pending jobs after readiness returns. A job interrupted before export can be tried again by uploading the image.

## Acceptance tests

- Use a stub worker to verify startup and busy readiness, all-or-nothing batch validation, file-specific errors, FIFO execution, stage updates, and download responses. Verify that a failed job leaves its queued siblings eligible to finish.
- Restart during a running job and between final GLB rename and the success record. Verify queue recovery, artifact recovery, failure reporting, and retained history. Verify the service preserves an unreadable JSON store.
- Run CUDA and attention checks inside the Windows container and generate a textured GLB from the bundled example. Record peak VRAM and elapsed time, or the last stage and error if the attempt fails. [Example input](../assets/example_image/T.png)
