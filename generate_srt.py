from srt import Subtitle, compose
from datetime import timedelta
from compile_transcript import compile_transcript
from pathlib import Path


def create_subtitles(input_path: Path) -> list[Subtitle]:
    """
    Use srt library to generate list of Subtitle objects from compiled transcript
    """
    subtitles: list[Subtitle] = []
    cues = compile_transcript(input_path=input_path)
    for i, cue in enumerate(cues):
        start = timedelta(milliseconds=cue.start)
        end = timedelta(milliseconds=cue.end)
        subtitles.append(Subtitle(index=i + 1, start=start, end=end, content=cue.text))

    return subtitles


def write_subtitles(subtitles: list[Subtitle], output_path: Path) -> None:
    """
    Compose list of subtitles in SRT-formatted string and write to `output_path`
    """

    subtitle_str = compose(subtitles=subtitles, reindex=False)
    print(subtitle_str)
    with open(file=output_path, mode="w", encoding="utf-8") as f:
        f.write(subtitle_str)
    return


def generate_srt(input_path: Path, output_path: Path) -> None:
    subtitles = create_subtitles(input_path=input_path)
    write_subtitles(subtitles=subtitles, output_path=output_path)
