import subprocess
from pathlib import Path

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


def process_audio(input_path: Path) -> list[Path] | None:
    """
    Recurse through a folder and find all processable video types
    -> convert to 16000Hz .flac, normalize audio levels, only accoutns for 
    front center speaker for dialogue. 
    ffmpeg is multithreaded so no multithreading.
    """
    temp_dir = Path("./temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    processed_files: list[Path] = []
    # Collect all files that match `VIDEO_TYPES`
    print(f"Finding processable files in {input_path}...")
    files = [f for f in input_path.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_TYPES]
    print(f"Found {len(files)} processable files!")
    for file in files:
        print(f"Processing file: {file}...")

        output_path = temp_dir / file.with_suffix(".flac").name

        command: list[str] = [
            "ffmpeg",
            "-i",
            f"{file}",  # input path
            "-map", f"0:a:m:language:eng",  # Only processes english audio streams
            "-vn",  # no video
            # filter tag, filter sets audio to mono -
            # only from front center speaker because thats where dialogue comes from.
            # dynaudnorm and loudnorm normalize volume so quiet dialogue is easier to pick up.
            "-af",
            "pan=mono|c0=FC,dynaudnorm,loudnorm=I=-16:TP=-1.5",
            "-ar",
            "16000",  # set sample rate to 16000Hz
            "-sample_fmt",
            "s16",  # sample format = s16
            "-acodec",
            "flac",  # flac codec: supported by Soniox, lossless compression
            f"{output_path}",
        ]
        subprocess.run(command, check=True)
        processed_files.append(output_path)

        print(f"Successfully processed {file}!")
    return processed_files
