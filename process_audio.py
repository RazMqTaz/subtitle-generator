import subprocess
import json
from pathlib import Path
from concurrent.futures import Future, ThreadPoolExecutor

from utils import FailureLog

VIDEO_TYPES = {
    ".mkv",
    ".mp4",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".ts",
    ".m2ts",
    ".wmv",
    ".flv",
}


def find_eng_stream(file: Path) -> int:
    """
    Use ffprobe to find english audio stream.
    Sometimes streams are labeled as 'und' or are in other languages.
    """
    command = [
        "ffprobe",
        "-v", "error",  # only display actual errors
        "-select_streams", "a",  # restricts output to audio only
        "-show_entries", "stream=index:stream_tags=language",  # controls which fields get printed
        "-print_format", "json",
        file,
    ]

    try:
        out = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe failed: {e.stderr.strip()}") from e
    streams = json.loads(out).get("streams", [])

    for i, stream in enumerate(streams):
        lang = stream.get("tags", {}).get("language", "").lower()
        if lang in ("eng", "en"):
            return i
    # no english stream, or all undefined. often und is english.
    return 0


def get_channel_count(file: Path, stream_idx: int) -> int:
    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", f"a:{stream_idx}",
        "-show_entries", "stream=channels",
        "-print_format", "json",
        f"{file}",
    ]
    out = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    streams = json.loads(out).get("streams", [])
    return streams[0].get("channels", 0) if streams else 0


def discover_files(input_path: Path, failure_log: FailureLog) -> list[Path]:
    """
    Search through `input_path` for all processable files.
    """

    files: list[Path] = []

    if input_path.is_file():
        if input_path.suffix.lower() in VIDEO_TYPES:
            return [input_path]
        else:
            failure_log.record(
                path=input_path, 
                stage="[File Discovery]", 
                error=f"Input file not in {VIDEO_TYPES}"
            )
        return []
    else:
        print(f"Finding processable files in {input_path}...")
        files = [
            f
            for f in input_path.rglob("*")
            if f.is_file() and f.suffix.lower() in VIDEO_TYPES
        ]
        print(f"Found {len(files)} processable files!")
    return files


def process_audio(
    input_path: Path, temp_dir: Path, failure_log: FailureLog
) -> list[Path]:
    """
    Convert processable files to 16000Hz .flac, normalize audio levels
    """

    processed_files: list[Path] = []
    
    files = discover_files(input_path=input_path, failure_log=failure_log)
    if not files:
        return []

    futures: list[tuple[Path, Path, Future]] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        if files is not None:
            for file in files:
                try:
                    print(f"Queued file for processing: {file}...")

                    # assumes unique filenames across `input_path, else collision and overwritting is possible.`
                    output_path = temp_dir / file.with_suffix(".flac").name

                    stream_idx = find_eng_stream(file=file)
                    channels = get_channel_count(file=file, stream_idx=stream_idx)
                    if channels >= 6:
                        # 5.1, 6.1, 7.1... most files should fit this
                        # gets audio only from front center speaker, where dialogue comes from
                        audio_filter = "pan=mono|c0=FC,dynaudnorm,loudnorm=I=-16:TP=-1.5"
                    else:
                        # mono or stero -> let ffmpeg do a clean stereo-mono downmix
                        audio_filter = (
                            "aformat=channel_layouts=mono,dynaudnorm,loudnorm=I=-16:TP=-1.5"
                        )
                    command: list[str] = [
                        "ffmpeg",
                        "-i", f"{file}",  # input path
                        "-map", f"0:a:{stream_idx}",  # Only processes english audio streams
                        "-vn",  # no video
                        "-af", f"{audio_filter}",
                        "-ar", "16000",  # set sample rate to 16000Hz
                        "-sample_fmt", "s16",
                        "-acodec", "flac",  # flac codec: supported by Soniox, lossless compression
                        f"{output_path}",
                    ]
                    future = ex.submit(subprocess.run, command, check=True)
                    futures.append((file, output_path, future))
                except Exception as e:
                    failure_log.record(path=file, stage="[Audio Processing]", error=str(e))
            
            for file, output_path, future in futures:
                try:
                    future.result()
                    processed_files.append(output_path)
                    print(f"Successfully processed {file}!")
                except Exception as e:
                    failure_log.record(path=file, stage="[Audio Processing]", error=str(e))
    return processed_files
