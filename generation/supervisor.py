import asyncio
import logging
import multiprocessing

from .store import STAGES, valid_glb, sync_dir
from .worker import worker_main

log = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, store, target=worker_main):
        self.store = store
        self.target = target
        self.context = multiprocessing.get_context("spawn")
        self.status = "starting"
        self.message = None
        self.store_ready = False
        self.child = None
        self.pipe = None
        self.active = None

    def launch(self):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=self.target, args=(child, self.store.directory))
        try:
            process.start()
        except Exception:
            parent.close()
            raise
        finally:
            child.close()
        self.child, self.pipe = process, parent

    async def stop_child(self):
        if self.child is None:
            return
        child = self.child
        self.pipe.close()
        if child.is_alive():
            child.terminate()
        await asyncio.to_thread(child.join, 5)
        if child.is_alive():
            child.kill()
            await asyncio.to_thread(child.join)
        child.close()
        self.child = self.pipe = None

    def unavailable(self, message):
        self.status, self.message = "unavailable", message

    def store_error(self):
        log.exception("Generation store failed")
        self.store_ready = False
        self.unavailable("The job store is unavailable. See generation container logs.")

    async def worker_lost(self, error):
        was_starting = self.status == "starting"
        self.status = "starting"
        await self.stop_child()
        self.store.recover_running(error)
        self.active = None
        if was_starting:
            self.unavailable("The generation worker exited during initialization.")
        else:
            self.start_worker()

    def start_worker(self):
        try:
            self.launch()
        except Exception:
            log.exception("Could not start worker")
            self.unavailable("Could not start the generation worker.")

    async def handle_message(self, kind, *parts):
        if kind == "ready" and self.status == "starting":
            self.status = "ready"
            return
        if kind == "init_error" and self.status == "starting":
            self.unavailable(parts[0])
            await self.stop_child()
            return
        if self.active is None or not parts or parts[0] != self.active.id:
            return
        job = self.active
        folder = self.store.directory / job.id
        partial = folder / "model.partial.glb"
        if kind == "stage":
            stage = parts[1]
            if stage in STAGES and (job.stage is None or STAGES.index(stage) > STAGES.index(job.stage)):
                self.active = self.store.update(job, stage=stage)
        elif kind == "artifact_ready":
            if valid_glb(partial, flush=True):
                partial.replace(folder / "model.glb")
                sync_dir(folder)
                self.store.finish(job)
            else:
                partial.unlink(missing_ok=True)
                self.store.finish(job, "The exporter produced an invalid GLB.")
            self.active = None
        elif kind == "job_failed":
            _, error, replace = parts
            if replace:
                await self.worker_lost(error)
            else:
                partial.unlink(missing_ok=True)
                self.store.finish(job, error)
                self.active = None

    async def dispatch(self):
        if self.status != "ready" or self.active is not None:
            return
        for job in self.store.jobs:
            if job.status != "queued":
                continue
            self.active = self.store.update(job, status="running")
            if not (self.store.directory / job.id / "input").is_file():
                self.store.finish(self.active, "The uploaded image is missing.")
                self.active = None
                continue
            try:
                self.pipe.send(("run", job.id))
            except (OSError, EOFError):
                await self.worker_lost("The generation worker exited.")
            return

    async def run(self):
        try:
            self.store.load()
            self.store_ready = True
            self.start_worker()
            while self.child is not None and self.store_ready:
                try:
                    incoming = self.pipe.recv() if self.pipe.poll() else None
                except (OSError, EOFError):
                    await self.worker_lost("The generation worker exited.")
                    continue
                if incoming:
                    await self.handle_message(*incoming)
                elif not self.child.is_alive():
                    await self.worker_lost("The generation worker exited.")
                if self.child is not None and self.child.is_alive():
                    await self.dispatch()
                await asyncio.sleep(0.05)
        except Exception:
            self.store_error()
        finally:
            await self.stop_child()
