import logging
import os
import time

from PIL import Image, ImageOps

log = logging.getLogger(__name__)
MODEL_REPOSITORY = "microsoft/TRELLIS.2-4B"
HIGH_RESOLUTION_MODELS = ("shape_slat_flow_model_1024", "tex_slat_flow_model_1024")


class CheckpointLoadError(RuntimeError):
    pass


def load_high_resolution_models(pipeline, load_model, stage):
    if all(name in pipeline.models for name in HIGH_RESOLUTION_MODELS):
        return
    stage("loading_checkpoints")
    checkpoints = pipeline._pretrained_args["models"]
    loaded = {
        name: load_model(MODEL_REPOSITORY, checkpoints[name]).eval()
        for name in HIGH_RESOLUTION_MODELS
    }
    pipeline.models.update(loaded)


def probe_cuda():
    import torch
    from trellis2.modules.attention import scaled_dot_product_attention
    from trellis2.modules.sparse import VarLenTensor
    from trellis2.modules.sparse.attention import sparse_scaled_dot_product_attention

    assert (torch.ones(8, device="cuda") * 2).sum().item() == 16
    qkv = torch.ones((1, 8, 3, 2, 64), dtype=torch.float16, device="cuda")
    dense = scaled_dot_product_attention(qkv)
    sparse = sparse_scaled_dot_product_attention(VarLenTensor(qkv[0], [slice(0, 8)]))
    torch.testing.assert_close(dense, torch.ones_like(dense))
    torch.testing.assert_close(sparse.feats, dense[0])
    print(f"CUDA and dense/sparse attention passed on {torch.cuda.get_device_name()}", flush=True)


def worker_main(pipe, directory):
    logging.basicConfig(level=logging.INFO)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    try:
        import torch
        probe_cuda()
        import o_voxel
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import GatedRepoError
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        from trellis2.pipelines.base import load_pretrained_model

        for gated_model in ("facebook/dinov3-vitl16-pretrain-lvd1689m", "briaai/RMBG-2.0"):
            try:
                hf_hub_download(gated_model, "config.json")
            except GatedRepoError:
                log.exception("Hugging Face denied access to %s", gated_model)
                pipe.send(("init_error", f"Hugging Face denied access to {gated_model}. Check HF_TOKEN and model access."))
                return

        class Pipeline512(Trellis2ImageTo3DPipeline):
            model_names_to_load = [
                name for name in Trellis2ImageTo3DPipeline.model_names_to_load if not name.endswith("_1024")
            ]

        pipeline = Pipeline512.from_pretrained(MODEL_REPOSITORY)
        pipeline.low_vram = True
        pipeline.cuda()
    except Exception:
        log.exception("TRELLIS initialization failed")
        pipe.send(("init_error", "TRELLIS initialization failed. See generation container logs."))
        return

    pipe.send(("ready",))
    while True:
        try:
            command, job_id, settings = pipe.recv()
        except EOFError:
            return
        if command != "run":
            return

        last_stage = None

        def stage(value):
            nonlocal last_stage
            if value == last_stage:
                return
            last_stage = value
            log.info("%s %s", job_id, value)
            pipe.send(("stage", job_id, value))

        started = time.monotonic()
        try:
            torch.cuda.reset_peak_memory_stats()
            folder = directory / job_id
            if settings.pipeline_type != "512":
                try:
                    load_high_resolution_models(pipeline, load_pretrained_model, stage)
                except torch.cuda.OutOfMemoryError:
                    raise
                except Exception as exc:
                    raise CheckpointLoadError("The high-resolution checkpoints could not be loaded.") from exc
            stage("preparing_image")
            with Image.open(folder / "input") as original:
                image = ImageOps.exif_transpose(original)
                has_alpha = any(band.lower() == "a" for band in image.getbands()) or "transparency" in image.info
                image = image.convert("RGBA" if has_alpha else "RGB")
            steps = {"steps": settings.sampling_steps}
            mesh = pipeline.run(
                image, pipeline_type=settings.pipeline_type, num_samples=1, seed=settings.seed,
                sparse_structure_sampler_params=steps, shape_slat_sampler_params=steps,
                tex_slat_sampler_params=steps, progress_callback=stage,
            )[0]
            stage("exporting_glb")
            glb = o_voxel.postprocess.to_glb(
                vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
                coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
                texture_size=settings.texture_size, decimation_target=settings.decimation_target, remesh=False, verbose=True,
            )
            glb.export(folder / "model.partial.glb", extension_webp=False)
            pipe.send(("artifact_ready", job_id, round(1 / mesh.voxel_size)))
            del mesh, glb, image
        except Exception as exc:
            log.exception("Generation failed for %s", job_id)
            cuda_failure = isinstance(exc, torch.cuda.OutOfMemoryError) or (
                isinstance(exc, RuntimeError) and any(
                    marker in str(exc).lower() for marker in ("cuda", "cublas", "cudnn", "illegal memory access")
                )
            )
            if isinstance(exc, torch.cuda.OutOfMemoryError):
                message = "The GPU ran out of memory."
            elif cuda_failure:
                message = "The GPU reported an error."
            elif isinstance(exc, CheckpointLoadError):
                message = str(exc)
            elif isinstance(exc, ValueError) and str(exc) == "No usable foreground found.":
                message = str(exc)
            else:
                message = "TRELLIS could not generate a GLB for this image. See container logs."
            pipe.send(("job_failed", job_id, message, cuda_failure))
            if cuda_failure:
                return
        finally:
            log.info("%s took %.1fs, peak CUDA memory %.0f MiB allocated, %.0f MiB reserved",
                     job_id, time.monotonic() - started, torch.cuda.max_memory_allocated() / 2**20,
                     torch.cuda.max_memory_reserved() / 2**20)


if __name__ == "__main__":
    probe_cuda()
