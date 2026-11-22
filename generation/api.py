import asyncio
import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from PIL import Image
from pydantic import BaseModel
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import FileResponse, JSONResponse

from .store import Job, Jobs, Store, basename, is_job_id
from .supervisor import Supervisor
from .worker import worker_main

log = logging.getLogger(__name__)
MIB = 1024**2
MAX_FILE = 10 * MIB
MAX_REQUEST = 82 * MIB
MAX_PIXELS = 16_000_000
NO_STORE = {"Cache-Control": "no-store"}


class ErrorDetail(BaseModel):
    code: str
    message: str
    file_index: int | None = None
    filename: str | None = None


class ErrorBody(BaseModel):
    error: ErrorDetail


class Health(BaseModel):
    status: Literal["ready", "starting", "unavailable"]
    message: str | None = None


class ApiError(Exception):
    def __init__(self, status, code, message, **fields):
        self.status = status
        self.detail = {"code": code, "message": message, **fields}


class UploadOverflow(MultiPartException):
    pass


class CompleteMultipartParser(MultiPartParser):
    complete = False

    def on_end(self):
        self.complete = True


def validate_upload(parts, staging):
    batch = []
    for index, (_, upload) in enumerate(parts):
        filename = basename(upload.filename)
        fields = {"file_index": index, "filename": filename}
        if upload.size > MAX_FILE:
            raise ApiError(413, "upload_too_large", "Each image must be at most 10 MiB.", **fields)
        try:
            with Image.open(upload.file) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ApiError(413, "upload_too_large", "Each image must be at most 16 megapixels.", **fields)
                if image.format not in {"JPEG", "PNG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Unsupported format or animation")
                image.load()
        except Image.DecompressionBombError:
            raise ApiError(413, "upload_too_large", "Each image must be at most 16 megapixels.", **fields)
        except (OSError, ValueError, SyntaxError, OverflowError):
            raise ApiError(400, "invalid_upload", "Provide a readable, static JPEG, PNG, or WebP image.", **fields)
        job = Job.queued(filename)
        folder = staging / job.id
        folder.mkdir()
        upload.file.seek(0)
        with (folder / "input").open("wb") as destination:
            shutil.copyfileobj(upload.file, destination)
            destination.flush()
            os.fsync(destination.fileno())
        batch.append(job)
    return batch


def errors(*statuses):
    descriptions = {400: "Invalid upload", 413: "Upload limit exceeded", 404: "Job or artifact not found",
                    409: "Job is not downloadable", 503: "Service or store unavailable", 500: "Internal error"}
    return {status: {"model": ErrorBody, "description": descriptions[status]} for status in statuses}


def create_app(root, worker=worker_main):
    store = Store(root)
    supervisor = Supervisor(store, worker)
    # State and persistence mutations run on the API event loop with no await
    # between readiness check and commit, or between claiming a job and sending it.

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(supervisor.run())
        try:
            yield
        finally:
            supervisor.status = "starting"
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await supervisor.stop_child()

    app = FastAPI(title="TRELLIS.2 Generation API", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status, headers=NO_STORE)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return await api_error(request, ApiError(400, "invalid_upload", "Invalid request."))

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        log.error("Request failed", exc_info=exc)
        return await api_error(request, ApiError(500, "internal_error", "An unexpected service error occurred."))

    def require_store():
        if not supervisor.store_ready:
            raise ApiError(503, "store_unavailable", "The job store is unavailable.")

    def require_ready():
        if supervisor.status != "ready" or not supervisor.store_ready:
            raise ApiError(503, "service_not_ready", "The generation worker is not ready.")

    def find_job(job_id):
        require_store()
        job = store.by_id.get(job_id) if is_job_id(job_id) else None
        if job is None:
            raise ApiError(404, "job_not_found", "Job not found.")
        return job

    @app.get("/health", response_model=Health, responses={503: {"model": Health}})
    async def health():
        body = {"status": supervisor.status}
        if supervisor.message and supervisor.status == "unavailable":
            body["message"] = supervisor.message
        return JSONResponse(body, status_code=200 if supervisor.status == "ready" else 503, headers=NO_STORE)

    @app.get("/jobs", response_model=Jobs, responses=errors(503))
    async def history():
        require_store()
        return JSONResponse({"jobs": [job.model_dump() for job in reversed(store.jobs)]}, headers=NO_STORE)

    @app.get("/jobs/{job_id}", response_model=Job, responses=errors(404, 503))
    async def details(job_id: str):
        return JSONResponse(find_job(job_id).model_dump(), headers=NO_STORE)

    @app.get("/jobs/{job_id}/download", response_class=FileResponse, responses={
        200: {"content": {"model/gltf-binary": {"schema": {"type": "string", "format": "binary"}}}},
        **errors(404, 409, 503),
    })
    async def download(job_id: str):
        job = find_job(job_id)
        if job.status != "succeeded":
            raise ApiError(409, "job_not_downloadable", "This job has no downloadable GLB.")
        path = store.directory / job.id / "model.glb"
        try:
            stat = path.stat()
        except FileNotFoundError:
            raise ApiError(404, "artifact_not_found", "The GLB file is missing.")
        except OSError:
            raise ApiError(503, "store_unavailable", "The GLB file could not be read.")
        name = Path(job.filename).stem
        name = "".join(character for character in name if character.isprintable()).strip(". ") or "model"
        filename = f"{name}.glb"
        fallback = "".join(
            character if character.isascii() and character not in '"\\' else "_" for character in filename
        )
        disposition = f'attachment; filename="{fallback}"'
        if filename != fallback:
            disposition += f"; filename*=UTF-8''{quote(filename, safe='')}"
        return FileResponse(path, stat_result=stat, media_type="model/gltf-binary",
                            headers={"Content-Disposition": disposition})

    @app.post("/jobs", status_code=202, response_model=Jobs, responses=errors(400, 413, 503, 500),
              openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
                  "schema": {"type": "object", "required": ["images"], "properties": {
                      "images": {"type": "array", "minItems": 1, "maxItems": 8,
                                 "items": {"type": "string", "format": "binary"}}
                  }},
                  "example": {"images": ["example.png"]},
              }}}})
    async def submit(request: Request):
        """Accept 1–8 still JPEG, PNG, or WebP images as one durable batch.

        A 202 returns queued records in upload order. A 400, 413, or 503
        guarantees that the batch was not accepted. Each file is limited to
        10 MiB and 16 megapixels, and the multipart body is limited to 82 MiB.
        """
        require_ready()
        length = request.headers.get("content-length")
        if length is not None and length.isdecimal() and int(length) > MAX_REQUEST:
            raise ApiError(413, "upload_too_large", "The request must be at most 82 MiB.")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "multipart/form-data":
            raise ApiError(400, "invalid_upload", "Send a multipart/form-data request with images.")

        async def limited_stream():
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_REQUEST:
                    raise UploadOverflow("The request must be at most 82 MiB.")
                yield chunk

        parser = CompleteMultipartParser(request.headers, limited_stream(), max_files=8, max_fields=0)
        try:
            form = await parser.parse()
        except UploadOverflow as exc:
            raise ApiError(413, "upload_too_large", exc.message)
        except Exception as exc:
            for file in parser._files_to_close_on_error:
                file.close()
            from python_multipart.exceptions import MultipartParseError
            if isinstance(exc, (MultiPartException, MultipartParseError)):
                raise ApiError(400, "invalid_upload", "Send 1–8 files in repeated images fields.")
            raise

        try:
            parts = form.multi_items()
            if not parser.complete:
                for file in parser._files_to_close_on_error:
                    file.close()
                raise ApiError(400, "invalid_upload", "The multipart request is incomplete.")
            if not parts or any(name != "images" or not isinstance(upload, UploadFile) for name, upload in parts):
                raise ApiError(400, "invalid_upload", "Send 1–8 files in repeated images fields.")
            with tempfile.TemporaryDirectory(dir=store.staging) as folder:
                staging = Path(folder)
                batch = await asyncio.to_thread(validate_upload, parts, staging)
                require_ready()
                try:
                    accepted = store.accept(batch, staging)
                except OSError:
                    supervisor.store_error()
                    raise ApiError(500, "internal_error", "The job batch could not be recorded.")
                return JSONResponse({"jobs": [job.model_dump() for job in accepted]}, status_code=202, headers=NO_STORE)
        finally:
            await form.close()

    return app


app = create_app(os.environ.get("DATA_DIR", "/data"))
