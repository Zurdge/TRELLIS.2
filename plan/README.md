# Local TRELLIS.2 application

Build a local image-to-3D application for a Windows PC with Docker Desktop, WSL 2, and a 12 GB NVIDIA GPU. One Compose command will launch the generation API and a separate browser frontend. The frontend will be available at `http://localhost:8080` and will enable submissions after the model is ready.

Each uploaded image will generate one independently tracked and downloadable `.glb`. Selecting several images queues one job per file in upload order. The stock TRELLIS.2 pipeline takes a single image per call. Job records and files will stay on a local Docker volume across container restarts and recreation. [Pipeline entry point](../trellis2/pipelines/trellis2_image_to_3d.py)

## Implementation documents

- [01 — Generation service and Windows Docker](01-generation-service.md) specifies the CUDA build, client-independent API, FIFO scheduling, progress, storage, and recovery.
- [02 — Frontend](02-frontend.md) specifies the React, Express, Tailwind CSS, and Zod setup, upload page, single active generation, and finished assets.
- [03 — Frontend implementation handoff](03-frontend-implementation.md) specifies the project files, component behavior, API proxy, and Docker integration for a developer.

## Implementation and verification order

1. Identify the Windows GPU and verify Docker GPU access. Build the matching generation image, check CUDA and attention kernels, and generate a 512³ GLB using the bundled example. Record elapsed time, peak VRAM, and the last reported stage.
2. Implement the job store, worker, and API. Use a stub generator to test the contracts and failure recovery independently of CUDA.
3. Implement the frontend against the same API contract, then wire both services together with Compose. Verify that a user can launch both with `docker compose up --build`, use the browser from the Windows host, and download finished assets.
4. Submit two images on the Windows PC, watch each job progress, and open both downloads in Blender to verify geometry and embedded materials. Recreate the containers and verify that history and completed downloads survive with the data volume retained.

The exact GPU model is unknown. The 512³ profile is the initial target for the 12 GB card, with end-to-end fit to be verified on that PC. [Example input](../assets/example_image/T.png), [Docker Desktop GPU support](https://docs.docker.com/desktop/features/gpu/)
