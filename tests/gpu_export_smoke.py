"""Exercise the GPU exporter with a textured synthetic mesh and no model weights."""

import json
import struct
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import o_voxel
import torch
import trimesh

from generation.store import valid_glb


def main():
    started = time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    box = trimesh.creation.box(extents=(0.5, 0.5, 0.5))
    axis = torch.arange(16, device="cuda", dtype=torch.int32)
    coords = torch.cartesian_prod(axis, axis, axis)
    attrs = torch.tensor([0.2, 0.7, 0.4, 0.1, 0.6, 1.0], device="cuda").repeat(len(coords), 1)
    result = o_voxel.postprocess.to_glb(
        vertices=torch.as_tensor(box.vertices, device="cuda", dtype=torch.float32),
        faces=torch.as_tensor(box.faces, device="cuda", dtype=torch.int32),
        attr_volume=attrs, coords=coords, grid_size=16,
        attr_layout={
            "base_color": slice(0, 3), "metallic": slice(3, 4),
            "roughness": slice(4, 5), "alpha": slice(5, 6),
        },
        aabb=[[-0.5] * 3, [0.5] * 3], texture_size=1024,
        decimation_target=100000, remesh=False,
    )
    with TemporaryDirectory() as directory:
        path = Path(directory) / "smoke.glb"
        result.export(path, extension_webp=False)
        assert valid_glb(path)
        with path.open("rb") as stream:
            stream.seek(12)
            length, chunk_type = struct.unpack("<I4s", stream.read(8))
            assert chunk_type == b"JSON"
            model = json.loads(stream.read(length))
        assert model["meshes"][0]["primitives"][0]["attributes"]["POSITION"] is not None
        assert "baseColorTexture" in model["materials"][0]["pbrMetallicRoughness"]
        assert all("bufferView" in image and image["mimeType"] == "image/png" for image in model["images"])
        print(f"Textured GLB export passed. {path.stat().st_size} bytes, {time.monotonic() - started:.1f}s, "
              f"{torch.cuda.max_memory_reserved() / 2**20:.0f} MiB peak Torch CUDA reserved memory.")


if __name__ == "__main__":
    main()
