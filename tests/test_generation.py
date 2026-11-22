import io
import json
import os
import struct
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

from generation.api import create_app, validate_upload, MAX_FILE, MAX_REQUEST
from generation.settings import DEFAULT_SETTINGS
from generation.store import Job, Store, STAGES
from generation.worker import HIGH_RESOLUTION_MODELS, MODEL_REPOSITORY, load_high_resolution_models

GLB = struct.pack("<4sIII4s4s", b"glTF", 2, 24, 4, b"JSON", b"{}  ")


def stub_worker(pipe, directory):
    time.sleep(0.15)
    pipe.send(("ready",))
    while True:
        _, job_id, settings = pipe.recv()
        folder = directory / job_id
        (folder / "stub-settings.json").write_text(settings.model_dump_json(), encoding="utf-8")
        with Image.open(folder / "input") as image:
            width = image.width
        for stage in STAGES:
            if stage == "loading_checkpoints" and settings.pipeline_type == "512":
                continue
            pipe.send(("stage", job_id, stage))
            time.sleep(float(os.environ.get("STUB_STAGE_SECONDS", "0.04")))
            if width == 13:
                return
        (folder / "model.partial.glb").write_bytes(GLB)
        actual = {"512": 512, "1024": 1024, "1024_cascade": 1024, "1536_cascade": 1280}
        if width == 14:
            pipe.send(("artifact_ready", job_id, "1280"))
        else:
            pipe.send(("artifact_ready", job_id, actual[settings.pipeline_type]))


def create_stub_app():
    return create_app(os.environ.get("DATA_DIR", "/tmp/trellis-stub"), stub_worker)


def stub_worker_with_failed_replacement(pipe, directory):
    initialized = directory.parent / "stub-initialized"
    if initialized.exists():
        pipe.send(("init_error", "Replacement initialization failed."))
        return
    initialized.touch()
    stub_worker(pipe, directory)


def image(width=3):
    stream = io.BytesIO()
    Image.new("RGB", (width, 3)).save(stream, "PNG")
    return stream.getvalue()


class GenerationTest(unittest.TestCase):
    def until(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.025)
        self.fail("Timed out waiting for the generation API")

    def test_jobs_and_worker_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            with TestClient(create_app(root, stub_worker)) as client:
                self.until(lambda: client.get("/jobs").status_code == 200)
                self.until(lambda: client.get("/health").status_code == 200)
                invalid = client.post("/jobs", files=[
                    ("images", ("valid.png", image())), ("images", ("invalid.png", b"bad")),
                ])
                self.assertEqual(invalid.status_code, 400)
                self.assertEqual(invalid.json()["error"]["file_index"], 1)
                self.assertEqual(client.get("/jobs").json(), {"jobs": []})
                too_large = client.post("/jobs", headers={"content-length": str(MAX_REQUEST + 1)})
                self.assertEqual(too_large.status_code, 413)
                incomplete = client.post("/jobs", content=b"--boundary\r\n",
                                         headers={"content-type": "multipart/form-data; boundary=boundary"})
                self.assertEqual(incomplete.status_code, 400)
                accepted = client.post("/jobs", files=[
                    ("images", ("first.png", image())),
                    ("images", ("crash.png", image(13))),
                    ("images", ("third.png", image())),
                ])
                self.assertEqual(accepted.status_code, 202)
                jobs = accepted.json()["jobs"]
                self.assertEqual([job["status"] for job in jobs], ["queued"] * 3)
                self.assertTrue(all(job["settings"] == DEFAULT_SETTINGS.model_dump() for job in jobs))
                self.assertEqual(client.get(f"/jobs/{jobs[-1]['id']}/download").status_code, 409)
                seen = []

                def settled():
                    response = client.get("/jobs")
                    self.assertEqual(response.status_code, 200)
                    history = response.json()["jobs"]
                    self.assertEqual([job["id"] for job in history], [job["id"] for job in reversed(jobs)])
                    running = [job["filename"] for job in history if job["status"] == "running"]
                    self.assertLessEqual(len(running), 1)
                    if running and running[0] not in seen:
                        seen.append(running[0])
                    return history if history[0]["status"] == "succeeded" else None

                history = self.until(settled)
                self.assertEqual(seen, ["first.png", "crash.png", "third.png"])
                self.assertEqual([job["status"] for job in history], ["succeeded", "failed", "succeeded"])
                result = client.get(history[0]["download_url"])
                self.assertEqual(result.content, GLB)
                self.assertEqual(result.headers["content-type"], "model/gltf-binary")
                self.assertEqual(result.headers["content-disposition"], 'attachment; filename="third.glb"')
                self.assertEqual(client.get("/jobs/bad").status_code, 404)
            with TestClient(create_app(root, stub_worker)) as client:
                self.assertEqual(self.until(lambda: client.get("/jobs").json().get("jobs")), history)
                self.assertEqual(client.get(history[0]["download_url"]).content, GLB)
                (Path(root) / "jobs" / history[0]["id"] / "model.glb").unlink()
                self.assertEqual(client.get(history[0]["download_url"]).status_code, 404)

    def test_settings_validation_and_image_order(self):
        file = ("images", ("model.png", image()))
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(root, stub_worker)) as client:
            self.until(lambda: client.get("/health").status_code == 200)
            openapi = client.get("/openapi.json").json()
            upload_schema = openapi["paths"]["/jobs"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
            self.assertEqual(upload_schema["properties"]["settings"]["type"], "string")
            self.assertEqual(upload_schema["properties"]["settings"]["contentSchema"]["properties"]["seed"]["default"], 42)
            mode_schema = upload_schema["properties"]["settings"]["contentSchema"]["properties"]["pipeline_type"]
            self.assertEqual(mode_schema["default"], "512")
            self.assertEqual(mode_schema["enum"], ["512", "1024", "1024_cascade", "1536_cascade"])
            invalid_settings = [
                "{", "[]", "null", '{"seed":0,"seed":1}', '{"extra":42}',
                '{"seed":true}', '{"seed":"0"}', '{"seed":-1}', '{"seed":2147483648}',
                '{"sampling_steps":7}', '{"sampling_steps":25}', '{"sampling_steps":8.5}',
                '{"texture_size":1024.0}', '{"texture_size":4096}', '{"texture_size":null}',
                '{"decimation_target":49999}', '{"decimation_target":500001}',
                '{"pipeline_type":1024}', '{"pipeline_type":null}', '{"pipeline_type":"2048_cascade"}',
            ]
            for value in invalid_settings:
                with self.subTest(value=value):
                    response = client.post("/jobs", files=[file, ("settings", (None, value))])
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["error"]["code"], "invalid_settings")
            for parts in [
                [("settings", (None, "{}".ljust(4097)))],
                [("settings", (None, "{}")), ("settings", (None, "{}"))],
                [("settings", ("settings.json", "{}"))],
                [("unexpected", (None, "{}"))],
            ]:
                response = client.post("/jobs", files=[file, *parts])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_upload")

            settings = ("settings", (None, '{"seed":0,"sampling_steps":24,"decimation_target":50000}'.ljust(4096)))
            images = [("images", (f"{i}.png", image())) for i in range(8)]
            response = client.post("/jobs", files=[
                *images[:2], settings, ("images", ("bad.png", b"bad")), *images[3:],
            ])
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["file_index"], 2)
            self.assertEqual(client.get("/jobs").json(), {"jobs": []})
            self.assertEqual(list((Path(root) / "staging").iterdir()), [])

            response = client.post("/jobs", files=[*images[:2], settings, *images[2:]])
            self.assertEqual(response.status_code, 202)
            jobs = response.json()["jobs"]
            self.assertEqual([job["filename"] for job in jobs], [f"{i}.png" for i in range(8)])
            expected = {"seed": 0, "sampling_steps": 24, "texture_size": 1024,
                        "decimation_target": 50_000, "pipeline_type": "512"}
            self.assertTrue(all(job["settings"] == expected for job in jobs))

    def test_settings_survive_restart_and_worker_replacement(self):
        def upload(client, filename, width, settings):
            response = client.post("/jobs", files=[
                ("images", (filename, image(width))), ("settings", (None, json.dumps(settings))),
            ])
            self.assertEqual(response.status_code, 202)
            return response.json()["jobs"][0]

        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"STUB_STAGE_SECONDS": "0.25"}):
            with TestClient(create_app(root, stub_worker_with_failed_replacement)) as client:
                self.until(lambda: client.get("/health").status_code == 200)
                # The first job exits during its first stage. Keep the next batch admitted before that stage finishes.
                first = upload(client, "crash.png", 13, {"seed": 0, "sampling_steps": 8, "pipeline_type": "1024_cascade"})
                next_job = upload(client, "waiting.png", 3, {
                    "seed": 2_147_483_647, "sampling_steps": 24, "texture_size": 2048, "decimation_target": 500_000,
                })
                self.until(lambda: client.get("/health").json()["status"] == "unavailable")
                history = client.get("/jobs").json()["jobs"]
                self.assertEqual([(job["id"], job["status"]) for job in history],
                                 [(next_job["id"], "queued"), (first["id"], "failed")])

            with TestClient(create_app(root, stub_worker)) as client:
                def finished():
                    response = client.get(f"/jobs/{next_job['id']}")
                    if response.status_code == 200 and response.json()["status"] == "succeeded":
                        return response.json()

                self.assertEqual(self.until(finished)["settings"], next_job["settings"])
                self.assertEqual(client.get(f"/jobs/{first['id']}").json()["settings"], first["settings"])
                for job in (first, next_job):
                    received = Path(root) / "jobs" / job["id"] / "stub-settings.json"
                    self.assertEqual(json.loads(received.read_text(encoding="utf-8")), job["settings"])

    def test_corrupt_index_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            index = Path(root) / "jobs.json"
            index.write_text("broken")
            with TestClient(create_app(root, stub_worker)) as client:
                self.until(lambda: client.get("/health").json()["status"] == "unavailable")
                self.assertEqual(client.get("/jobs").status_code, 503)
                self.assertEqual(index.read_text(), "broken")

    def test_generation_modes_and_invalid_completion_metadata(self):
        expected = {"512": 512, "1024": 1024, "1024_cascade": 1024, "1536_cascade": 1280}
        with tempfile.TemporaryDirectory() as root:
            with TestClient(create_app(root, stub_worker)) as client:
                self.until(lambda: client.get("/health").status_code == 200)
                pending = {}
                for mode in expected:
                    response = client.post("/jobs", files=[
                        ("images", (f"{mode}.png", image())),
                        ("settings", (None, json.dumps({"pipeline_type": mode}))),
                    ])
                    self.assertEqual(response.status_code, 202)
                    job = response.json()["jobs"][0]
                    self.assertEqual(job["settings"]["pipeline_type"], mode)
                    self.assertIsNone(job["actual_resolution"])
                    pending[mode] = job["id"]
                bad = client.post("/jobs", files={"images": ("bad-resolution.png", image(14))}).json()["jobs"][0]
                def finished():
                    rows = {job["id"]: job for job in client.get("/jobs").json()["jobs"]}
                    return rows if rows[bad["id"]]["status"] == "failed" else None
                history = self.until(finished)
                self.assertEqual({mode: history[job_id]["actual_resolution"]
                                  for mode, job_id in pending.items()}, expected)
                self.assertIsNone(history[bad["id"]]["actual_resolution"])
                self.assertEqual(client.get("/health").status_code, 200)
            with TestClient(create_app(root, stub_worker)) as client:
                self.until(lambda: client.get("/jobs").status_code == 200)
                recovered = client.get(f"/jobs/{pending['1536_cascade']}").json()
                self.assertEqual(recovered["actual_resolution"], 1280)
                self.assertEqual(recovered["settings"]["pipeline_type"], "1536_cascade")

    def test_lazy_checkpoints_are_published_only_as_a_complete_pair(self):
        pipeline = SimpleNamespace(models={"existing": object()},
                                   _pretrained_args={"models": {key: f"ckpts/{key}" for key in HIGH_RESOLUTION_MODELS}})
        shape, material = Mock(), Mock()
        shape.eval.return_value, material.eval.return_value = shape, material
        stage = Mock()
        failed_load = Mock(side_effect=[shape, OSError("Missing material checkpoint")])
        with self.assertRaises(OSError):
            load_high_resolution_models(pipeline, failed_load, stage)
        self.assertEqual(set(pipeline.models), {"existing"})
        load = Mock(side_effect=[shape, material])
        load_high_resolution_models(pipeline, load, stage)
        load_high_resolution_models(pipeline, load, stage)
        self.assertEqual(load.call_count, 2)
        for name, model in zip(HIGH_RESOLUTION_MODELS, (shape, material)):
            load.assert_any_call(MODEL_REPOSITORY, f"ckpts/{name}")
            self.assertIs(pipeline.models[name], model)
        self.assertEqual(shape.eval.call_count, 2)
        material.eval.assert_called_once()
        self.assertEqual(stage.call_count, 2)

    def test_concurrent_batches_use_commit_order(self):
        entered, release = Event(), Event()

        def hold_first(parts, staging, settings):
            if parts[0][1].filename == "slow-1.png":
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("First batch was not released")
            return validate_upload(parts, staging, settings)

        def submit(client, prefix):
            return client.post("/jobs", files=[
                ("images", (f"{prefix}-1.png", image())), ("images", (f"{prefix}-2.png", image())),
            ])

        with tempfile.TemporaryDirectory() as root, TestClient(create_app(root, stub_worker)) as client:
            self.until(lambda: client.get("/health").status_code == 200)
            with patch("generation.api.validate_upload", hold_first), ThreadPoolExecutor() as pool:
                first = pool.submit(submit, client, "slow")
                try:
                    self.assertTrue(entered.wait(10))
                    second = submit(client, "fast")
                    self.assertEqual(second.status_code, 202)
                finally:
                    release.set()
                self.assertEqual(first.result().status_code, 202)
            names = [job["filename"] for job in client.get("/jobs").json()["jobs"]]
            self.assertEqual(names, ["slow-2.png", "slow-1.png", "fast-2.png", "fast-1.png"])

    def test_upload_limits(self):
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(root, stub_worker)) as client:
            self.until(lambda: client.get("/health").status_code == 200)
            png = image()
            oversized = png.ljust(MAX_FILE + 1, b"\0")
            response = client.post("/jobs", files={"images": ("large.png", oversized)})
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json()["error"]["filename"], "large.png")
            pixel_limit = io.BytesIO()
            Image.new("1", (4000, 4001)).save(pixel_limit, "PNG")
            response = client.post("/jobs", files={"images": ("pixels.png", pixel_limit.getvalue())})
            self.assertEqual(response.status_code, 413)

            def chunks():
                yield b'--boundary\r\nContent-Disposition: form-data; name="images"; filename="stream.png"\r\n\r\n'
                block = b"x" * (1024**2)
                for _ in range(82):
                    yield block

            response = client.post("/jobs", content=chunks(),
                                   headers={"content-type": "multipart/form-data; boundary=boundary"})
            self.assertEqual(response.status_code, 413)
            self.assertEqual(client.get("/jobs").json(), {"jobs": []})
            exact = png.ljust(MAX_FILE, b"\0")
            self.assertEqual(client.post("/jobs", files={"images": ("exact.png", exact)}).status_code, 202)
            exact_pixels = io.BytesIO()
            Image.new("1", (4000, 4000)).save(exact_pixels, "PNG")
            self.assertEqual(client.post(
                "/jobs", files={"images": ("exact-pixels.png", exact_pixels.getvalue())}
            ).status_code, 202)

    def test_recovery_after_artifact_rename(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.load()
            job = Job.queued("recovered.png")
            store.commit([job])
            store.update(job, status="running", stage="exporting_glb")
            folder = store.directory / job.id
            folder.mkdir()
            (folder / "model.glb").write_bytes(GLB)
            (folder / "model.partial.glb").write_bytes(b"partial")
            persisted = json.loads(store.index.read_text(encoding="utf-8"))
            del persisted["jobs"][0]["settings"]
            store.index.write_text(json.dumps(persisted), encoding="utf-8")
            with TestClient(create_app(root, stub_worker)) as client:
                self.until(lambda: client.get("/jobs").status_code == 200)
                recovered = client.get(f"/jobs/{job.id}").json()
                self.assertEqual(recovered["status"], "succeeded")
                self.assertEqual(recovered["settings"], DEFAULT_SETTINGS.model_dump())
                self.assertIsNone(recovered["actual_resolution"])
                self.assertEqual(client.get(recovered["download_url"]).content, GLB)
                self.assertFalse((folder / "model.partial.glb").exists())

    def test_interrupted_job_keeps_custom_settings(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.load()
            settings = DEFAULT_SETTINGS.model_copy(update={"seed": 17, "texture_size": 2048})
            job = Job.queued("interrupted.png", settings)
            store.commit([job])
            store.update(job, status="running", stage="generating_shape")
            folder = store.directory / job.id
            folder.mkdir()
            (folder / "input").write_bytes(image())
            persisted = json.loads(store.index.read_text(encoding="utf-8"))
            del persisted["jobs"][0]["settings"]["pipeline_type"]
            del persisted["jobs"][0]["actual_resolution"]
            store.index.write_text(json.dumps(persisted), encoding="utf-8")
            with TestClient(create_app(root, stub_worker)) as client:
                self.until(lambda: client.get("/jobs").status_code == 200)
                recovered = client.get(f"/jobs/{job.id}").json()
                self.assertEqual(recovered["status"], "failed")
                self.assertEqual(recovered["settings"], settings.model_dump())

    def test_recovery_preserves_resolution_only_after_promotion(self):
        for promoted in (False, True):
            with self.subTest(promoted=promoted), tempfile.TemporaryDirectory() as root:
                store = Store(root)
                store.load()
                settings = DEFAULT_SETTINGS.model_copy(update={"pipeline_type": "1536_cascade"})
                job = Job.queued("interrupted.png", settings)
                store.commit([job])
                running = store.update(job, status="running", stage="exporting_glb", actual_resolution=1280)
                self.assertIsNone(running.public_record()["actual_resolution"])
                folder = store.directory / job.id
                folder.mkdir()
                (folder / ("model.glb" if promoted else "model.partial.glb")).write_bytes(GLB)
                with TestClient(create_app(root, stub_worker)) as client:
                    self.until(lambda: client.get("/jobs").status_code == 200)
                    recovered = client.get(f"/jobs/{job.id}").json()
                    self.assertEqual(recovered["status"], "succeeded" if promoted else "failed")
                    self.assertEqual(recovered["actual_resolution"], 1280 if promoted else None)
                    self.assertEqual(recovered["settings"]["pipeline_type"], "1536_cascade")

    def test_replacement_failure_preserves_history_and_pauses_admission(self):
        with tempfile.TemporaryDirectory() as root, TestClient(
            create_app(root, stub_worker_with_failed_replacement)
        ) as client:
            self.until(lambda: client.get("/health").status_code == 200)
            accepted = client.post("/jobs", files=[
                ("images", ("finished.png", image())),
                ("images", ("crash.png", image(13))),
                ("images", ("waiting.png", image())),
            ])
            self.assertEqual(accepted.status_code, 202)
            self.until(lambda: client.get("/health").json()["status"] == "unavailable")
            jobs = client.get("/jobs").json()["jobs"]
            self.assertEqual([job["status"] for job in jobs], ["queued", "failed", "succeeded"])
            self.assertEqual(client.get(jobs[-1]["download_url"]).content, GLB)
            response = client.post("/jobs", files={"images": ("new.png", image())})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "service_not_ready")
