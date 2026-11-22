import io
import os
import struct
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from generation.api import create_app, validate_upload, MAX_FILE, MAX_REQUEST
from generation.store import Job, Store, STAGES

GLB = struct.pack("<4sIII4s4s", b"glTF", 2, 24, 4, b"JSON", b"{}  ")


def stub_worker(pipe, directory):
    time.sleep(0.15)
    pipe.send(("ready",))
    while True:
        _, job_id = pipe.recv()
        folder = directory / job_id
        with Image.open(folder / "input") as image:
            crash = image.width == 13
        for stage in STAGES:
            pipe.send(("stage", job_id, stage))
            time.sleep(float(os.environ.get("STUB_STAGE_SECONDS", "0.04")))
            if crash:
                return
        (folder / "model.partial.glb").write_bytes(GLB)
        pipe.send(("artifact_ready", job_id))


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

    def test_corrupt_index_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            index = Path(root) / "jobs.json"
            index.write_text("broken")
            with TestClient(create_app(root, stub_worker)) as client:
                self.until(lambda: client.get("/health").json()["status"] == "unavailable")
                self.assertEqual(client.get("/jobs").status_code, 503)
                self.assertEqual(index.read_text(), "broken")

    def test_concurrent_batches_use_commit_order(self):
        entered, release = Event(), Event()

        def hold_first(parts, staging):
            if parts[0][1].filename == "slow-1.png":
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("First batch was not released")
            return validate_upload(parts, staging)

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
            with TestClient(create_app(root, stub_worker)) as client:
                self.until(lambda: client.get("/jobs").status_code == 200)
                recovered = client.get(f"/jobs/{job.id}").json()
                self.assertEqual(recovered["status"], "succeeded")
                self.assertEqual(client.get(recovered["download_url"]).content, GLB)
                self.assertFalse((folder / "model.partial.glb").exists())

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
