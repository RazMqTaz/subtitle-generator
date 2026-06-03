from pathlib import Path
from threading import Lock
from typing import TypedDict

class FailureLog:
    """
    Reuseable FailureLog class with two methods:
    record() records an error inside a threadlock
    all() returns all failures
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._failures: list[dict] = []

    def record(self, path: Path, stage: str, error: str) -> None:
        with self._lock:
            print(f"FAILED {path.name} during stage {stage}: {error}")
            self._failures.append({"file": path, "stage": stage, "error": error})

    def all(self) -> list[dict]:
        return list(self._failures)
    
class Context(TypedDict):
    terms: list[str]