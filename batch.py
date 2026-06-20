import os

from requests import Session
from requests.adapters import HTTPAdapter
import soundfile as sf

from concurrent.futures import Future, ThreadPoolExecutor, as_completed

from threading import Condition

from pathlib import Path
from dataclasses import dataclass
from generate_transcript import generate_transcript
from generate_srt import generate_srt
from utils import FailureLog, Context, PROGRESS_COLUMNS
from rich.progress import Progress
from urllib3.util.retry import Retry
from generate_transcript import delete_all_files, delete_all_transcriptions

# 10GB, between uploading, processing, and stored
SONIOX_BYTE_BUDGET = 10 * 1024**3
SONIOX_DURATION_LIMIT = 18000.0

# Requests created but not yet processing, Soniox supports up 100, so 99 to be safe.
# Soniox does support up to 2,000 total transcriptions (pending + completed + failed)
# In theory we could decouple uploads from polling, but efficiency gains would not be that much.
SONIOX_MAX_CONCURRENT_JOBS = 99


@dataclass
class Job:
    session: Session
    audio_path: Path
    transcript_path: Path
    subtitle_path: Path
    language_hints: list[str] | None
    kept_languages: list[str] | None
    context: Context
    failure_log: FailureLog


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


def build_session() -> Session:
    """
    Build Soniox session at top level to allow deleting previous sessions
    stored transcriptions / uploaded files.
    """
    api_key = os.getenv("SONIOX_API_KEY")
    if not api_key:
        raise RuntimeError("Missing SONIOX_API_KEY")

    session = Session()
    session.headers["Authorization"] = f"Bearer {api_key}"

    retries = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429],
        allowed_methods=None,
        respect_retry_after_header=True,
    )
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=retries,
            pool_maxsize=SONIOX_MAX_CONCURRENT_JOBS,
            pool_connections=SONIOX_MAX_CONCURRENT_JOBS,
        ),
    )
    return session


def is_processable(file_duration: float, file_size: int) -> bool:
    """
    Simply checks if the file is too large or too long to process.
    """
    if file_size > SONIOX_BYTE_BUDGET or file_duration > SONIOX_DURATION_LIMIT:
        return False
    return True


def resolve_subtitle_language(
    kept_languages: list[str] | None,
    language_hints: list[str] | None,
) -> str | None:
    """
    Pick the ISO 639-1 code that best represents the SRT's language, used for the
    Jellyfin filename tag (e.g. name.default.en.srt). Priority: kept_languages
    (literally what's filtered into the output), then the first language hint.
    Returns None when no language is configured.
    """
    if kept_languages:
        return kept_languages[0]
    if language_hints:
        return language_hints[0]
    return None


def run_job(
    job: Job,
    budget: ByteBudget,
    file_size: int,
) -> None:
    """
    Defines one job execution.
    Reserves byte budget -> generates subtitles (assuming available worker)
    -> releases byte budget.
    """
    budget.reserve(file_size=file_size)
    try:
        generate_transcript(
            session=job.session,
            audio_path=job.audio_path,
            language_hints=job.language_hints,
            output_path=job.transcript_path,
            context=job.context,
            failure_log=job.failure_log,
        )
        generate_srt(
            input_path=job.transcript_path,
            output_path=job.subtitle_path,
            kept_languages=job.kept_languages,
        )
    except Exception as e:
        job.failure_log.record(path=job.audio_path, stage="[Job]", error=str(e))
    finally:
        budget.release(file_size=file_size)


def run_batch(
    processed_audio_files: list[Path],
    source_paths: dict[Path, Path],
    temp_dir: Path,
    output_dir: Path | None,
    language_hints: list[str] | None,
    kept_languages: list[str] | None,
    default: bool,
    context: dict[Path, Context],
    failure_log: FailureLog,
) -> None:
    """
    Run batch of run_job() instances.
    """
    budget = ByteBudget()
    with build_session() as session:
        # First delete all transcriptions and uploaded files to clear space
        delete_all_transcriptions(session=session)
        delete_all_files(session=session)
        with Progress(*PROGRESS_COLUMNS) as progress:
            task = progress.add_task(
                "Generating subtitles", total=len(processed_audio_files)
            )
            with ThreadPoolExecutor(max_workers=SONIOX_MAX_CONCURRENT_JOBS) as executor:
                future_map: dict[Future, Path] = {}
                for file in processed_audio_files:
                    file_size = file.stat().st_size
                    file_duration = sf.info(file=file).duration
                    if is_processable(file_duration=file_duration, file_size=file_size):
                        transcript_path = temp_dir / f"{file.stem}.transcript.json"
                        # assumes unique filenames across `input_path`, otherwise collision is possible.
                        # Jellyfin reads the language code + `.default` flag from the filename suffix
                        # chain (e.g. name.default.en.srt) to label and pre-select the track.
                        lang = resolve_subtitle_language(
                            kept_languages=kept_languages,
                            language_hints=language_hints,
                        )
                        parts = [file.stem]
                        if default:
                            parts.append("default")
                        if lang:
                            parts.append(lang)
                        # No --output-dir: write the SRT next to its source video (where
                        # Jellyfin/Plex auto-detect external subs). Otherwise flat-dump
                        # everything into output_dir.
                        dest_dir = (
                            output_dir
                            if output_dir is not None
                            else source_paths[file].parent
                        )
                        subtitle_path = dest_dir / f"{'.'.join(parts)}.srt"
                        job = Job(
                            session=session,
                            audio_path=file,
                            transcript_path=transcript_path,
                            subtitle_path=subtitle_path,
                            language_hints=language_hints,
                            kept_languages=kept_languages,
                            context=context[file],
                            failure_log=failure_log,
                        )
                        future = executor.submit(run_job, job, budget, file_size)
                        future_map[future] = file
                    else:
                        msg = (
                            "File either exceeds 10GB or 300 minute duration."
                            f"\nProcessed file size: {file_size / (1024**3):.2f}GB / 10GB"
                            f"\nProcessed file duration: {file_duration:.0f} seconds / {SONIOX_DURATION_LIMIT} seconds"
                        )
                        failure_log.record(
                            path=file, stage="[Batch Execution]", error=msg
                        )
                        progress.advance(task)

                for fut in as_completed(future_map):
                    file = future_map[fut]
                    try:
                        fut.result()
                    except Exception as e:
                        failure_log.record(
                            path=file,
                            stage="[Worker]",
                            error=f"Worker died unexpectedly: {e}",
                        )
                    finally:
                        progress.advance(task)
