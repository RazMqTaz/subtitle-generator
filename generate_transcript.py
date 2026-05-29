import os
import time
import json

import requests
from dotenv import load_dotenv
from requests import Session
from pathlib import Path

from typing import Optional

SONIOX_API_BASE_URL = "https://api.soniox.com"

load_dotenv()


def _check_status(res: requests.Response) -> None:
    try:
        res.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(f"{e}\nResponse body: {res.text}", response=res) from e


def get_config(
    file_id: Optional[str],
    translation: Optional[str],
    language_hints: Optional[list[str]],
) -> dict:
    config = {
        "model": "stt-async-v4",
        "language_hints": language_hints,
        "language_hints_strict": True,
        "enable_language_identification": True,
        "file_id": file_id,
    }

    if translation is None:
        pass
    elif translation == "one_way":
        config["translation"] = {
            "type": "one_way",
            "target_language": "es",
        }
    else:
        raise ValueError(f"Unsupported translation {translation}")

    return config


def upload_audio(session: Session, audio_path: Path) -> str:
    print("Starting file upload...")
    with open(audio_path, "rb") as f:
        res = session.post(f"{SONIOX_API_BASE_URL}/v1/files", files={"file": f})
    _check_status(res=res)
    file_id = res.json()["id"]
    print(f"File ID: {file_id}")
    return file_id


def create_transcription(session: Session, config: dict) -> str:
    print("Creating transcription...")
    res = session.post(
        f"{SONIOX_API_BASE_URL}/v1/transcriptions",
        json=config,
    )
    _check_status(res=res)
    transcription_id = res.json()["id"]
    print(f"Transcription ID: {transcription_id}")
    return transcription_id


def wait_until_completed(session: Session, transcription_id: str) -> None:
    print("Waiting for transcription...")
    while True:
        res = session.get(f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}")
        _check_status(res=res)
        data = res.json()
        if data["status"] == "completed":
            return
        elif data["status"] == "error":
            raise Exception(f"Error: {data.get('error_message', 'Unknown error')}")
        time.sleep(1)


def get_transcription(session: Session, transcription_id: str) -> dict:
    res = session.get(
        f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}/transcript"
    )
    _check_status(res=res)
    return res.json()


def parser_delete(session: Session, transcription_id: str) -> None:
    res = session.delete(f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}")
    _check_status(res=res)


def parser_delete_file(session: Session, file_id: str) -> None:
    res = session.delete(f"{SONIOX_API_BASE_URL}/v1/files/{file_id}")
    _check_status(res=res)


def delete_all_files(session: Session) -> None:
    files: list[dict] = []
    cursor: str = ""

    while True:
        print("Retrieving files...")
        res = session.get(f"{SONIOX_API_BASE_URL}/v1/files?cursor={cursor}")
        _check_status(res=res)
        res_json = res.json()
        files.extend(res_json["files"])
        cursor = res_json["next_page_cursor"]
        if cursor is None:
            break

    total = len(files)
    if total == 0:
        print("No files to delete.")
        return

    print(f"Deleting {total} files...")
    for idx, file in enumerate(files):
        file_id = file["id"]
        print(f"Deleting file: {file_id} ({idx + 1}/{total})")
        parser_delete_file(session=session, file_id=file_id)
    print(f"Deleted {total} files.")


def delete_all_transcriptions(session: Session) -> None:
    transcriptions: list[dict] = []
    cursor: str = ""

    while True:
        print("Retrieving transcriptions...")
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
        print("No transcriptions to delete.")
        return

    print(f"Deleting {total} transcriptions...")
    for idx, transcription in enumerate(transcriptions):
        transcription_id = transcription["id"]
        print(f"Deleting transcription: {transcription_id} ({idx + 1}/{total})")
        parser_delete(session=session, transcription_id=transcription_id)
    print(f"Deleted {total} transcriptions.")


def transcribe_file(
    session: Session,
    audio_path: Path,
    translation: Optional[str],
    language_hints: Optional[list[str]],
    output_path: Path,
) -> None:
    file_id = upload_audio(session=session, audio_path=audio_path)

    config = get_config(
        file_id=file_id,
        translation=translation,
        language_hints=language_hints,
    )

    transcription_id = create_transcription(session=session, config=config)

    wait_until_completed(session=session, transcription_id=transcription_id)

    result = get_transcription(session=session, transcription_id=transcription_id)

    # text = render_tokens(final_tokens=result["tokens"])

    with open(file=output_path, mode="w", encoding="utf-8") as f:
        json.dump(result, f)

    parser_delete(session=session, transcription_id=transcription_id)

    if file_id is not None:
        parser_delete_file(session=session, file_id=file_id)


def generate_transcript(
    audio_path: Path,
    translation: Optional[str],
    language_hints: Optional[list[str]],
    output_path: Path = Path("transcript.json"),
) -> None:

    api_key = os.getenv("SONIOX_API_KEY")
    if not api_key:
        raise RuntimeError("Missing SONIOX_API_KEY")

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {api_key}"

    transcribe_file(
        session=session,
        audio_path=audio_path,
        translation=translation,
        language_hints=language_hints,
        output_path=output_path,
    )
