import threading
import time
from uuid import uuid4

ORIGIN = "chrome-extension://iepjdaiobphnnkmbphialgkfiedohmmg"


class BrowserBridge:
    def __init__(self):
        self.condition = threading.Condition()
        self.jobs = {}
        self.last_poll = None
        self.pause_until = 0
        self.version = ""
        self.last_result = None

    def connected(self):
        with self.condition:
            return self.last_poll is not None and time.monotonic() - self.last_poll < 90

    def diagnostics(self):
        with self.condition:
            return {"version": self.version, "last_result": self.last_result}

    def next_job(self, wait=False, version=""):
        with self.condition:
            self.last_poll = time.monotonic()
            self.version = version
            deadline = self.last_poll + (12 if wait else 0)
            while True:
                if self.pause_until > time.monotonic():
                    return None
                if not any(job["claimed"] and job["answer"] is None for job in self.jobs.values()):
                    for key, job in self.jobs.items():
                        if not job["claimed"]:
                            job["claimed"] = True
                            return {"id": key, "url": job["url"]}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)

    def complete(self, key, result):
        with self.condition:
            job = self.jobs.get(key)
            if not job or not job["claimed"] or job["answer"] is not None:
                return False
            job["answer"] = result
            self.last_result = {"status": result["status"], "stage": result.get("stage", ""), "time": time.time()}
            if result["status"] == "verification":
                self.pause_until = time.monotonic() + 900
            self.condition.notify_all()
            return True

    def query(self, url):
        with self.condition:
            if self.pause_until > time.monotonic():
                return {"status": "verification"}
            if len(self.jobs) >= 4:
                return {"status": "busy"}
            key = uuid4().hex
            job = self.jobs[key] = {"url": url, "claimed": False, "answer": None}
            self.condition.notify_all()
            try:
                self.condition.wait_for(lambda: job["answer"] is not None, timeout=60)
                if job["answer"] is not None:
                    return job["answer"]
                stage = "response" if job["claimed"] else "waiting"
                self.last_result = {"status": "timeout", "stage": stage, "time": time.time()}
                return {"status": "timeout", "stage": stage}
            finally:
                self.jobs.pop(key, None)
                self.condition.notify_all()
