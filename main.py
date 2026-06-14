import argparse
import os
import shutil
import sys
import requests
from pathlib import Path

from batch import run_batch
from process_audio import process_audio
from generate_context import generate_context
from utils import FailureLog


def get_languages() -> list[str]:
    """
    Queries Soniox models endpoint for supported languages.
    """
    api_key = os.getenv("SONIOX_API_KEY")
    if not api_key:
        raise RuntimeError("Missing SONIOX_API_KEY")

    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get("https://api.soniox.com/v1/models", headers=headers)
    response.raise_for_status()
    data = response.json()
    model = next((m for m in data["models"] if m["id"] == "stt-async-v5"))
    return [lang["code"] for lang in model["languages"]]


def check_languages(args: argparse.Namespace) -> None:
    """
    Checks that any enabled language settings are in available languages.
    """

    if args.translation or args.keep_only or args.language_hints:
        supported_languages = get_languages()
        if args.translation and args.translation not in supported_languages:
            raise SystemExit(
                f"Target language {args.translation} is not in available languages."
                f"Available languages are: {'\n'.join(supported_languages)}"
            )
        if args.language_hints:
            unsupported = [lang for lang in args.language_hints if lang not in supported_languages]
            if unsupported:
                raise SystemExit(
                    f"One or more language hint(s) {args.language_hints} is not in available languages."
                    f"Available languages are: {'\n'.join(supported_languages)}"
                )
        if args.keep_only:
            unsupported = [lang for lang in args.keep_only if lang not in supported_languages]
            if unsupported:
                raise SystemExit(
                    f"One or more keep only language(s) {args.keep_only} is not in available languages."
                    f"Available languages are: {'\n'.join(supported_languages)}"
                )
        

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Subtitle Generator", description="Generate subtitles for a movie!"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the full pipeline")
    run.add_argument("--media-path", type=Path, required=True)
    run.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Flat-dump all SRTs into this folder; if omitted, each SRT is written next to its source video",
    )
    run.add_argument("--language-hints", nargs="*")
    run.add_argument("--keep-only", nargs="*", help="Drop transcript tokens not in this language code")
    run.add_argument("--translation", type=str)
    run.add_argument(
        "--default",
        action="store_true",
        help="Tag the SRT as the default subtitle track (adds .default to the filename for Jellyfin)",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()
    temp_dir = Path(os.getenv("SUBGEN_TEMP_DIR", "./temp"))
    if args.command == "run":
        try:
            
            # in case process was interruped before cleaning /temp
            shutil.rmtree(temp_dir, ignore_errors=True)
            failure_log = FailureLog()

            print("Subtitle Generator v0.1.0, built by Erazem Mattick, powered by Soniox.")

            check_languages(args=args)

            REQUIRED = ["ANTHROPIC_API_KEY", "TMDB_READ_ACCESS_TOKEN", "SONIOX_API_KEY"]
            missing = [k for k in REQUIRED if not os.getenv(k)]
            if missing:
                raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

            if args.output_dir is not None:
                args.output_dir.mkdir(parents=True, exist_ok=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            audio_sources = process_audio(
                args.media_path, temp_dir=temp_dir, failure_log=failure_log
            )
            if audio_sources:
                survivors, context = generate_context(
                    files=list(audio_sources), failure_log=failure_log
                )
                if survivors:
                    run_batch(
                        processed_audio_files=survivors,
                        source_paths=audio_sources,
                        temp_dir=temp_dir,
                        output_dir=args.output_dir,
                        language_hints=args.language_hints,
                        kept_languages=args.keep_only,
                        translation=args.translation,
                        default=args.default,
                        context=context,
                        failure_log=failure_log,
                    )

            failures = failure_log.all()
            if failures:
                print(f"\n{len(failures)} file(s) failed:")
                for failure in failures:
                    print(
                        f"    - {failure['file']} during stage {failure['stage']}: {failure['error']}"
                    )
                sys.exit(1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
