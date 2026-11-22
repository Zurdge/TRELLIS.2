import json
import os
import re
import shutil
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, get_args
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, model_validator

Stage = Literal[
    "preparing_image", "extracting_features", "generating_structure",
    "generating_shape", "generating_materials", "decoding_mesh", "exporting_glb",
]
STAGES = get_args(Stage)
UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_job_id(value):
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except ValueError:
        return False


def basename(filename):
    return filename.replace("\\", "/").rsplit("/", 1)[-1].replace("\0", "") or "image"


class Job(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_extra={"examples": [{
        "id": "1a707a1f-afb9-4200-bda4-801de2634831",
        "filename": "example.png",
        "status": "queued",
        "stage": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "error": None,
        "download_url": None,
    }]})

    id: str
    filename: str
    status: Literal["queued", "running", "succeeded", "failed"]
    stage: Stage | None
    created_at: str
    updated_at: str
    error: str | None
    download_url: str | None

    @model_validator(mode="after")
    def validate_record(self):
        if not is_job_id(self.id) or self.filename != basename(self.filename):
            raise ValueError("Invalid job identity")
        for timestamp in (self.created_at, self.updated_at):
            if not UTC_TIMESTAMP.fullmatch(timestamp):
                raise ValueError("Expected a UTC timestamp")
            datetime.fromisoformat(timestamp[:-1] + "+00:00")
        if self.status != "running" and self.stage is not None:
            raise ValueError("Stage is only valid for a running job")
        expected_url = f"/jobs/{self.id}/download" if self.status == "succeeded" else None
        if self.download_url != expected_url:
            raise ValueError("Invalid download URL")
        if (self.status == "failed" and not self.error) or (self.status != "failed" and self.error is not None):
            raise ValueError("Invalid job error")
        return self

    @classmethod
    def queued(cls, filename):
        timestamp = now()
        return cls(id=str(uuid4()), filename=filename, status="queued", stage=None,
                   created_at=timestamp, updated_at=timestamp, error=None, download_url=None)


class Jobs(BaseModel):
    jobs: list[Job]


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def valid_glb(path, flush=False):
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12:
                return False
            magic, version, length = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2 or length < 20 or length != os.fstat(stream.fileno()).st_size:
                return False
            if flush:
                os.fsync(stream.fileno())
            return True
    except FileNotFoundError:
        return False


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.index = self.root / "jobs.json"
        self.directory = self.root / "jobs"
        self.staging = self.root / "staging"
        self.jobs = []
        self.by_id = {}

    def load(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.index.exists():
            if self.directory.exists() and any(self.directory.iterdir()):
                raise ValueError("Job index is missing")
            self.commit([])
        data = json.loads(self.index.read_text(encoding="utf-8"))
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("Unsupported job index version")
        jobs = Jobs.model_validate(data).jobs
        if len({job.id for job in jobs}) != len(jobs) or sum(job.status == "running" for job in jobs) > 1:
            raise ValueError("Inconsistent job index")
        self.jobs = jobs
        self.by_id = {job.id: job for job in jobs}
        self.directory.mkdir(exist_ok=True)
        self.staging.mkdir(exist_ok=True)
        for path in self.staging.iterdir():
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        for path in self.directory.iterdir():
            if path.name not in self.by_id:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
            else:
                (path / "model.partial.glb").unlink(missing_ok=True)
        self.index.with_suffix(".tmp").unlink(missing_ok=True)
        self.recover_running("Generation was interrupted by a service restart.")

    def commit(self, jobs):
        with self.index.with_suffix(".tmp").open("w", encoding="utf-8") as stream:
            json.dump({"version": 1, "jobs": [job.model_dump() for job in jobs]}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        self.index.with_suffix(".tmp").replace(self.index)
        sync_dir(self.root)
        self.jobs = jobs
        self.by_id = {job.id: job for job in jobs}

    def accept(self, batch, staging):
        for job in batch:
            source = staging / job.id
            sync_dir(source)
            source.rename(self.directory / job.id)
        sync_dir(self.directory)
        timestamp = now()
        batch = [job.model_copy(update={"created_at": timestamp, "updated_at": timestamp}) for job in batch]
        self.commit(self.jobs + batch)
        return batch

    def update(self, job, **changes):
        updated = job.model_copy(update={"updated_at": now(), **changes})
        self.commit([updated if item.id == job.id else item for item in self.jobs])
        return updated

    def finish(self, job, error=None):
        return self.update(job, status="failed" if error else "succeeded", stage=None,
                           error=error, download_url=None if error else f"/jobs/{job.id}/download")

    def recover_running(self, error):
        for job in self.jobs:
            if job.status == "running":
                folder = self.directory / job.id
                (folder / "model.partial.glb").unlink(missing_ok=True)
                if valid_glb(folder / "model.glb", flush=True):
                    sync_dir(folder)
                    self.finish(job)
                else:
                    self.finish(job, error)
