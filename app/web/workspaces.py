"""문서마다 임시 작업을 발급하고 닫힌 작업의 파일을 정리한다."""

import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Work:
    app: object
    touched: float
    active: int = 0
    closed: bool = False


class Workspaces:
    def __init__(self, factory, ttl=120, interval=5):
        self.factory, self.ttl = factory, ttl
        self.items = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self.thread.start()

    def new(self):
        app = self.factory()
        token = uuid.uuid4().hex
        app.url_prefix = f"/work/{token}"
        with self.lock:
            self.items[token] = Work(app, time.monotonic())
        return app.url_prefix

    @contextmanager
    def use(self, token):
        with self.lock:
            item = self.items.get(token)
            if not item or item.closed:
                raise FileNotFoundError(
                    "작업이 종료되었습니다. 새로고침해 다시 시작하세요."
                )
            item.active += 1
            item.touched = time.monotonic()
        try:
            yield item.app
        finally:
            with self.lock:
                item.active -= 1
                item.touched = time.monotonic()
            self.sweep()

    def release(self, token):
        with self.lock:
            if token in self.items:
                self.items[token].closed = True
        self.sweep()

    def sweep(self):
        with self.lock:
            now = time.monotonic()
            ready = []
            for token, item in self.items.items():
                if now - item.touched >= self.ttl:
                    item.closed = True
                if (
                    item.closed
                    and not item.active
                    and not any(t.is_alive() for t in item.app.threads)
                ):
                    ready.append(token)
            apps = [self.items.pop(token).app for token in ready]
        for app in apps:
            app.close()

    def _run(self, interval):
        while not self.stop.wait(interval):
            self.sweep()

    def close(self):
        self.stop.set()
        self.thread.join()
        with self.lock:
            for item in self.items.values():
                item.closed = True
        self.sweep()
