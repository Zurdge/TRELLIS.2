# Run the local TRELLIS.2 application

Docker Compose starts two Linux containers. Express serves the browser app at
`http://127.0.0.1:8080`. The FastAPI generation service runs at
`http://generation:8000` on the Compose network and uses one NVIDIA GPU.

The default build targets the RTX 3060 with compute capability 8.6. Use Docker
Desktop with the WSL 2 backend and an NVIDIA Windows driver. Initialize the
repository's Eigen submodule before building.

```powershell
git submodule update --init --recursive
docker run --rm --gpus all ubuntu:22.04 nvidia-smi --query-gpu=name,compute_cap,memory.total --format=csv
```

Request access to [DINOv3](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)
and [RMBG 2.0](https://huggingface.co/briaai/RMBG-2.0) on Hugging Face. Put a read
token for the approved account in the local `.env` file as `HF_TOKEN=...`, or
set `HF_TOKEN` in the PowerShell environment. The token is passed at runtime.

```powershell
docker compose up -d --build
docker compose logs -f generation
```

Model initialization downloads checkpoints into the Hugging Face cache volume.
The page enables submissions once the worker reports Ready. The data volume
holds uploaded images, job history, and successful GLBs across container
recreation. Preserve the named volumes when taking the app down.

## Direct API and GPU check

Publish the API on the host loopback interface when working with direct HTTP
clients. Interactive API documentation is available at
`http://127.0.0.1:8000/docs`.

```powershell
docker compose -f compose.yaml -f compose.local.yaml up -d generation
docker compose exec generation python3 -m generation.worker
curl.exe http://127.0.0.1:8000/health
curl.exe -F "images=@assets/example_image/T.png" http://127.0.0.1:8000/jobs
curl.exe http://127.0.0.1:8000/jobs
```

Poll the job until it succeeds, then download its `download_url`. The generation
log includes each stage, elapsed time, and peak PyTorch CUDA memory. The API
starts serving persisted history even when model initialization fails.

To check the native GLB exporter separately from the model, run the synthetic
mesh test in the generation container.

```powershell
Get-Content tests/gpu_export_smoke.py -Raw | docker compose exec -T generation python3 -
```

## API acceptance with a stub worker

Run the CPU-only tests from the repository root on Windows.

```powershell
docker run --rm --mount "type=bind,source=$($PWD.Path),target=/app,readonly" -w /app python:3.10-slim sh -c "pip -q install fastapi==0.128.0 python-multipart==0.0.22 Pillow==12.1.1 httpx && python -m unittest discover -s tests -v"
```

## Windows validation

On the RTX 3060 (12,288 MiB), the 512³ worker generated two textured GLBs in
one batch through the Express proxy. The jobs ran in sequence and took 171.4 s
and 163.2 s, including export. Peak board usage was 7,614 MiB across the batch,
including other GPU activity. Peak PyTorch CUDA reserved memory was 2,866 MiB
for `T.png` and 4,450 MiB for the second example (the WebP beginning with
`0e4984a9`).

Blender 5.3 imported both GLBs and found geometry and two packed texture images
in each. Both job records and byte-identical downloads survived recreation of
the generation and frontend containers.
