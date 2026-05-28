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


def process_audio(input_path: Path) -> Path | None:
    print(f"Processing file: {input_path}...")
    if input_path.suffix not in VIDEO_TYPES:
        print(
            f"File type {input_path.suffix} not allowed."
            f" Allowed media types are: {", ".join(type for type in VIDEO_TYPES)}"
        )
        return
    output_path = input_path.with_suffix(".flac")

    command: list[str] = [
        "ffmpeg",
        "-i",
        f"{input_path}",  # input path
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

    print(f"Successfully processed {input_path}!")
    return output_path
