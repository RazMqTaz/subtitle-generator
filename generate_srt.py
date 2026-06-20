from srt import Subtitle, compose
from datetime import timedelta
from compile_transcript import compile_transcript
from pathlib import Path

ATTRIBUTION_TEXT = (
    "Transcribed by RazMqTaz\nhttps://github.com/RazMqTaz/subtitle-generator"
)
ATTRIBUTION_DURATION_MS = 4000


def create_subtitles(
    input_path: Path, kept_languages: list[str] | None
) -> list[Subtitle]:
    """
    Use srt library to generate list of Subtitle objects from compiled transcript
    """
    # Attribution cue shown from 0s; dialogue cues follow starting at index 2.
    subtitles: list[Subtitle] = [
        Subtitle(
            index=1,
            start=timedelta(0),
            end=timedelta(milliseconds=ATTRIBUTION_DURATION_MS),
            content=ATTRIBUTION_TEXT,
        )
    ]
    cues = compile_transcript(input_path=input_path, kept_languages=kept_languages)
    for i, cue in enumerate(cues):
        start = timedelta(milliseconds=cue.start)
        end = timedelta(milliseconds=cue.end)
        subtitles.append(Subtitle(index=i + 2, start=start, end=end, content=cue.text))

    return subtitles


def write_subtitles(subtitles: list[Subtitle], output_path: Path) -> None:
    """
    Compose list of subtitles in SRT-formatted string and write to `output_path`
    """

    subtitle_str = compose(subtitles=subtitles, reindex=False)
    with open(file=output_path, mode="w", encoding="utf-8") as f:
        f.write(subtitle_str)
    return


def generate_srt(
    input_path: Path, output_path: Path, kept_languages: list[str] | None
) -> None:
    subtitles = create_subtitles(input_path=input_path, kept_languages=kept_languages)
    write_subtitles(subtitles=subtitles, output_path=output_path)
