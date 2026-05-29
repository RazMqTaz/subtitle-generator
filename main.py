import argparse
import shutil

from pathlib import Path

from batch import run_batch
from process_audio import process_audio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Subtitle Generator", description="Generate subtitles for a movie!"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the full pipeline")
    run_source = run.add_mutually_exclusive_group(required=True)
    run_source.add_argument("--audio-path", type=Path)
    run.add_argument("--output-dir", type=Path, default=Path("./out"))
    run.add_argument("--language-hints", nargs="*")
    run.add_argument("--translation", choices=["one_way"])

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "run":
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        processed_audio_files = process_audio(args.audio_path)
        if processed_audio_files is None:
            print(
                f"Something went wrong with audio processing. File path: {args.audio_path}"
            )
            return
        run_batch(
            processed_audio_files=processed_audio_files,
            output_dir=args.output_dir,
            language_hints=args.language_hints,
            translation=args.translation,
        )
    shutil.rmtree(Path("./temp"))


if __name__ == "__main__":
    main()
