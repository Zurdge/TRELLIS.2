FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS gpu
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 CUDA_HOME=/usr/local/cuda \
    ATTN_BACKEND=flash_attn SPARSE_ATTN_BACKEND=flash_attn HF_HOME=/cache/huggingface
ARG TORCH_CUDA_ARCH_LIST=8.6
ARG MAX_JOBS=2
ENV TORCH_CUDA_ARCH_LIST=$TORCH_CUDA_ARCH_LIST MAX_JOBS=$MAX_JOBS
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-dev git build-essential ninja-build libglib2.0-0 libgl1 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --upgrade 'pip<26' setuptools wheel packaging && \
    python3 -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
COPY generation/requirements.txt /tmp/requirements.txt
RUN python3 -m pip install -r /tmp/requirements.txt
RUN python3 -m pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.3/flash_attn-2.7.3%2Bcu12torch2.6cxx11abiFALSE-cp310-cp310-linux_x86_64.whl
RUN python3 -m pip install --no-build-isolation \
    git+https://github.com/JeffreyXiang/CuMesh.git@12289e1062f0603f2f0d0771b02e1395d247f26f
RUN python3 -m pip install --no-build-isolation \
    git+https://github.com/JeffreyXiang/FlexGEMM.git@6dd94a859c26ee8246888502eada3dd8ad85532e \
    git+https://github.com/NVlabs/nvdiffrast.git@v0.4.0
COPY o-voxel /tmp/o-voxel
# Its source dependencies are already installed at the revisions above.
RUN python3 -m pip install --no-build-isolation --no-deps /tmp/o-voxel && rm -rf /tmp/o-voxel
WORKDIR /app
COPY trellis2 trellis2
COPY generation generation
CMD ["python3", "-m", "uvicorn", "generation.api:app", "--host", "0.0.0.0", "--port", "8000"]
