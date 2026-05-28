import argparse

from pathlib import Path

from generate_transcript import generate_transcript
from generate_srt import generate_srt
from process_audio import process_audio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Subtitle Generator", description="Generate subtitles for a movie!"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the full pipeline")
    run_source = run.add_mutually_exclusive_group(required=True)
    run_source.add_argument("--audio-path", type=Path)
    run_source.add_argument("--audio-url")
    run.add_argument("--output-dir", type=Path, default=Path("./out"))
    run.add_argument("--enable-speaker-diarization", action="store_true")
    run.add_argument("--language-hints", nargs="*")
    run.add_argument("--translation", choices=["one_way"])

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "run":
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        transcript_path = Path(args.output_dir) / "transcript.json"
        processed_audio_path = process_audio(args.audio_path)
        if processed_audio_path is None:
            print(
                f"Something went wrong with audio processing. File path: {args.audio_path}"
            )
            return
        generate_transcript(
            audio_path=processed_audio_path,
            audio_url=args.audio_url,
            language_hints=args.language_hints,
            enable_speaker_diarization=False,
            translation=None,
            output_path=transcript_path,
        )
        generate_srt(
            input_path=transcript_path, output_path=Path(args.output_dir) / "output.srt"
        )
        if transcript_path.exists():
            processed_audio_path.unlink(missing_ok=True)
            print(f"Successfully cleaned up processed audio file: {processed_audio_path}!")


if __name__ == "__main__":
    main()
