import soundfile as sf

from concurrent.futures import ThreadPoolExecutor

from threading import BoundedSemaphore, Condition

from pathlib import Path
from dataclasses import dataclass
from generate_transcript import generate_transcript
from generate_srt import generate_srt

# 10GB, between uploading, processing, and stored
SONIOX_BYTE_BUDGET = 10 * 1024**3
SONIOX_DURATION_LIMIT = 18000.0


@dataclass
class Job:
    audio_path: Path
    output_dir: Path
    transcript_path: Path
    subtitle_path: Path
    language_hints: list[str]
    translation: str


class ByteBudget:
    """
    Two methods:
    reserve() waits until theres space and then adds that file to budget
    release() releases those bytes
    """

    def __init__(self) -> None:
        self._cond = Condition()
        self._used = 0

    def reserve(self, file_size: int) -> None:
        with self._cond:
            while self._used + file_size >= SONIOX_BYTE_BUDGET:
                self._cond.wait()
            self._used += file_size

    def release(self, file_size: int) -> None:
        with self._cond:
            self._used -= file_size
            self._cond.notify_all()


def run_job(
    job: Job, sem: BoundedSemaphore, budget: ByteBudget, file_size: int
) -> None:
    """
    Defines one job execution.
    Reserves byte budget -> generates subtitles (assuming available worker)
    -> releases byte budget.
    """
    budget.reserve(file_size=file_size)
    try:
        with sem:
            generate_transcript(
                audio_path=job.audio_path,
                translation=job.translation,
                language_hints=job.language_hints,
                output_path=job.transcript_path,
            )
            generate_srt(input_path=job.transcript_path, output_path=job.subtitle_path)
    finally:
        budget.release(file_size=file_size)


def run_batch(
    processed_audio_files: list[Path],
    output_dir: Path,
    language_hints: list[str],
    translation: str,
) -> None:
    """
    Run batch of run_job() instances.
    """
    temp_dir = Path("./temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    # Soniox allows 100 concurrent jobs, 99 to be safe
    sem = BoundedSemaphore(99)
    budget = ByteBudget()
    with ThreadPoolExecutor(max_workers=99) as executor:
        for file in processed_audio_files:
            file_size = file.stat().st_size
            file_duration = sf.info(file=file).duration
            if file_size > SONIOX_BYTE_BUDGET or file_duration > SONIOX_DURATION_LIMIT:
                print(
                    f"File {file} is too large or too long to process."
                    f"\nSize: {file_size}B / {SONIOX_BYTE_BUDGET}B. allowed."
                    f"\nDuration: {file_duration}s / {SONIOX_DURATION_LIMIT}s allowed."
                )
                continue
            transcript_path = temp_dir / f"{file.stem}.transcript.json"
            subtitle_path = output_dir / file.with_suffix(".srt").name
            job = Job(
                audio_path=file,
                output_dir=output_dir,
                transcript_path=transcript_path,
                subtitle_path=subtitle_path,
                language_hints=language_hints,
                translation=translation,
            )
            executor.submit(run_job, job, sem, budget, file_size)
