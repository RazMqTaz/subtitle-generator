import argparse
import os
import shutil

from pathlib import Path

from batch import run_batch
from process_audio import process_audio
from generate_context import generate_context


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Subtitle Generator", description="Generate subtitles for a movie!"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the full pipeline")
    run.add_argument("--media-path", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, default=Path("./out"))
    run.add_argument("--language-hints", nargs="*")
    run.add_argument("--translation", choices=["one_way"])

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "run":

        REQUIRED = ["ANTHROPIC_API_KEY", "TMDB_READ_ACCESS_TOKEN", "SONIOX_API_KEY"]
        missing = [k for k in REQUIRED if not os.getenv(k)]
        if missing:
            raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        processed_audio_files = process_audio(args.media_path)
        if not processed_audio_files:
            print(
                f"Something went wrong with audio processing. File path: {args.media_path}"
            )
            return
        context = generate_context(files=processed_audio_files)
        run_batch(
            processed_audio_files=processed_audio_files,
            output_dir=args.output_dir,
            language_hints=args.language_hints,
            translation=args.translation,
            context=context,
        )
    shutil.rmtree(Path("./temp"), ignore_errors=True)


if __name__ == "__main__":
    main()
