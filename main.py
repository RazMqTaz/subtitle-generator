import os, time, argparse

from dotenv import load_dotenv

from typing import Optional

import requests
from requests import Session

SONIOX_API_BASE_URL = "https://api.soniox.com"

load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 'transcribe' subcommand
    transcribe = subparsers.add_parser("transcribe", help="Transcribe audio")
    source = transcribe.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--audio-url", help="Public URL of the audio file to transcribe."
    )
    source.add_argument(
        "--audio-path", help="Path to a local audio file to transcribe."
    )
    transcribe.add_argument("--enable-speaker-diarization", action="store_true")
    transcribe.add_argument("--language-hints", nargs="*")
    transcribe.add_argument("--translation", choices=["one_way"])

    # 'delete-transcription' subcommand
    parser_delete = subparsers.add_parser(
        "delete-transcription", help="Delete a transcription."
    )
    parser_delete.add_argument("transcription_id")

    # 'delete-file' subcommand
    parser_delete_file = subparsers.add_parser(
        "delete-file", help="Delete an uploaded file."
    )
    parser_delete_file.add_argument("file_id")

    # 'delete-all-transcriptions' command
    subparsers.add_parser(
        "delete-all-transcriptions", help="Delete all stored transcriptions."
    )

    # 'delete-all-files' subcommand
    subparsers.add_parser("delete-all-files", help="Delete every uploaded file.")

    return parser


def _check_status(res: requests.Response) -> None:
    try:
        res.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(f"{e}\nResponse body: {res.text}", response=res) from e


def get_config(
    audio_url: Optional[str],
    file_id: Optional[str],
    translation: Optional[str],
    language_hints: Optional[list[str]],
    enable_speaker_diarization: bool = False,
) -> dict:
    config = {
        "model": "stt-async-v4",
        "language_hints": language_hints,
        "enable_language_identification": True,
        "enable_speaker_diarization": enable_speaker_diarization,
        "audio_url": audio_url,
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


def upload_audio(session: Session, audio_path: str) -> str:
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


def render_tokens(final_tokens: list[dict]) -> str:
    chunks: list[dict] = []
    current_speaker: Optional[str] = None
    current_language: Optional[str] = None
    start_ms: int
    end_ms: int

    # Process all tokens in order
    for token in final_tokens:
        chunk: dict = {"speaker": None, "start_ms": 0, "end_ms": 0, "text": ""}
        text = token["text"]
        speaker = token.get("speaker")
        language = token.get("language")
        start_ms = token["start_ms"]
        end_ms = token["end_ms"]
        is_translation = token.get("translation_status") == "translation"

        # Speaker has changed -> create new chunk
        if speaker is not None and speaker != current_speaker:
            if current_speaker is not None:
                chunks.append(chunk["speaker"] = speaker
            current_speaker = speaker
            current_language = None  # Reset language on speaker changes
            

        # Language has changed -> add a language tag
        if language is not None and language != current_language:
            current_language = language
            prefix = "[Translation] " if is_translation else ""
            text_parts.append(f"{prefix}[{current_language}] ")
            text = text.lstrip()

        text_parts.append(text)

    return "".join(text_parts)


def transcribe_file(
    session: Session,
    audio_url: Optional[str],
    audio_path: Optional[str],
    translation: Optional[str],
    language_hints: Optional[list[str]],
    enable_speaker_diarization: bool,
) -> None:
    if audio_url is not None:
        file_id = None
    elif audio_path is not None:
        file_id = upload_audio(session=session, audio_path=audio_path)
    else:
        raise ValueError("Missing audio: audio_url or audio_path must be specified.")

    config = get_config(
        audio_url=audio_url,
        file_id=file_id,
        translation=translation,
        language_hints=language_hints,
        enable_speaker_diarization=enable_speaker_diarization,
    )

    transcription_id = create_transcription(session=session, config=config)

    wait_until_completed(session=session, transcription_id=transcription_id)

    result = get_transcription(session=session, transcription_id=transcription_id)

    text = render_tokens(final_tokens=result["tokens"])

    print(text)

    parser_delete(session=session, transcription_id=transcription_id)

    if file_id is not None:
        parser_delete_file(session=session, file_id=file_id)


def main():
    args = build_parser().parse_args()

    api_key = os.getenv("SONIOX_API_KEY")
    if not api_key:
        raise RuntimeError("Missing SONIOX_API_KEY")

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {api_key}"

    if args.command == "transcribe":
        transcribe_file(
            session=session,
            audio_url=args.audio_url,
            audio_path=args.audio_path,
            translation=args.translation,
            language_hints=args.language_hints,
            enable_speaker_diarization=args.enable_speaker_diarization,
        )
    elif args.command == "delete-transcription":
        parser_delete(session, args.transcription_id)
    elif args.command == "delete-file":
        parser_delete_file(session=session, file_id=args.file_id)
    elif args.command == "delete-all-transcriptions":
        delete_all_transcriptions(session=session)
    elif args.command == "delete-all-files":
        delete_all_files(session=session)


if __name__ == "__main__":
    main()
