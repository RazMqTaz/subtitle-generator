import os
import time
import json
import random

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from requests import Session
from pathlib import Path
from utils import Context, FailureLog

load_dotenv()

SONIOX_API_BASE_URL = os.getenv("SONIOX_API_BASE_URL", "https://api.soniox.com")


MAX_WAIT = 3600  # seconds before ending transcribe job


def _check_status(res: requests.Response) -> None:
    try:
        res.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(f"{e}\nResponse body: {res.text}", response=res) from e


def get_config(
    file_id: str | None,
    language_hints: list[str] | None,
    context: Context | None,
) -> dict:
    config = {
        "model": "stt-async-v5",
        "language_hints": language_hints,
        "language_hints_strict": True,
        "enable_language_identification": True,
        "file_id": file_id,
    }
    if context and context.get("terms"):
        config["context"] = context

    return config


def upload_audio(session: Session, audio_path: Path) -> str:
    file_size = audio_path.stat().st_size / (1024**2)
    with open(audio_path, "rb") as f:
        res = session.post(f"{SONIOX_API_BASE_URL}/v1/files", files={"file": f})
    _check_status(res=res)
    file_id = res.json()["id"]
    return file_id


def create_transcription(session: Session, config: dict) -> str:
    res = session.post(
        f"{SONIOX_API_BASE_URL}/v1/transcriptions",
        json=config,
    )
    _check_status(res=res)
    transcription_id = res.json()["id"]
    return transcription_id


def wait_until_completed(session: Session, transcription_id: str) -> None:
    time_taken = 0
    while time_taken <= MAX_WAIT:
        res = session.get(f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}")
        _check_status(res=res)
        data = res.json()
        if data["status"] == "completed":
            return
        elif data["status"] == "error":
            raise RuntimeError(f"Error: {data.get('error_message', 'Unknown error')}")
        random_time = 10 + random.uniform(0, 5)
        # Polls every 10 sec + some random jitter to prevent 429 limit exceeded error.
        time.sleep(random_time)
        time_taken += random_time
    raise TimeoutError(
        f"File took too long to transcribe (>= {MAX_WAIT // 60} minutes)."
    )


def get_transcription(session: Session, transcription_id: str) -> dict:
    res = session.get(
        f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}/transcript"
    )
    _check_status(res=res)
    return res.json()


def delete_transcription(session: Session, transcription_id: str) -> None:
    res = session.delete(f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}")
    _check_status(res=res)


def delete_file(session: Session, file_id: str) -> None:
    res = session.delete(f"{SONIOX_API_BASE_URL}/v1/files/{file_id}")
    _check_status(res=res)


def delete_all_files(session: Session) -> None:
    files: list[dict] = []
    cursor: str = ""

    while True:
        res = session.get(f"{SONIOX_API_BASE_URL}/v1/files?cursor={cursor}")
        _check_status(res=res)
        res_json = res.json()
        files.extend(res_json["files"])
        cursor = res_json["next_page_cursor"]
        if cursor is None:
            break

    total = len(files)
    if total == 0:
        return

    for idx, file in enumerate(files):
        file_id = file["id"]
        delete_file(session=session, file_id=file_id)


def delete_all_transcriptions(session: Session) -> None:
    transcriptions: list[dict] = []
    cursor: str = ""

    while True:
        res = session.get(f"{SONIOX_API_BASE_URL}/v1/transcriptions?cursor={cursor}")
        _check_status(res=res)
        res_json = res.json()
        for transcription in res_json["transcriptions"]:
            status = transcription["status"]
            # Delete only transcriptions with completed or error status
            if status == "completed" or status == "error":
                transcriptions.append(transcription)
        cursor = res_json["next_page_cursor"]
        if cursor is None:
            break

    total = len(transcriptions)
    if total == 0:
        return

    for transcription in transcriptions:
        transcription_id = transcription["id"]
        delete_transcription(session=session, transcription_id=transcription_id)


def generate_transcript(
    session: Session,
    audio_path: Path,
    language_hints: list[str] | None,
    context: Context | None,
    output_path: Path,
    failure_log: FailureLog,
) -> None:
    file_id = None
    transcription_id = None
    try:
        file_id = upload_audio(session=session, audio_path=audio_path)

        config = get_config(
            file_id=file_id,
            language_hints=language_hints,
            context=context,
        )

        transcription_id = create_transcription(session=session, config=config)
        wait_until_completed(
            session=session,
            transcription_id=transcription_id,
        )

        result = get_transcription(session=session, transcription_id=transcription_id)

        with open(file=output_path, mode="w", encoding="utf-8") as f:
            json.dump(result, f)

    finally:
        try:
            if transcription_id is not None:
                delete_transcription(session=session, transcription_id=transcription_id)
        except Exception as e:
            failure_log.record(
                path=audio_path,
                stage="[Cleanup]",
                error=(
                    f"Warning: transcription cleanup failed for {transcription_id}: {e}"
                ),
            )

        try:
            if file_id is not None:
                delete_file(session=session, file_id=file_id)
        except Exception as e:
            failure_log.record(
                path=audio_path,
                stage="[Cleanup]",
                error=(f"Warning: file cleanup failed for {file_id}: {e}"),
            )
