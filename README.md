# Subtitle Generator

Generates SRT subtitle files for video using Soniox's async speech-to-text. Built as a demo of what the Soniox API can do when you wrap it with some practical scaffolding (audio extraction, vocabulary hints, cue timing).

Point it at a video file or a folder of videos and it spits out SRT files.

## What it actually does

1. Pulls the English audio out of each video with ffmpeg, downmixes to mono, normalizes loudness so quiet dialogue is easier to pick up.
2. Parses the filename to guess title, season, episode (via `guessit`).
3. Looks the title up on TMDB to grab cast/character names.
4. Asks Claude for any series-specific proper nouns the ASR model might mangle (place names, factions, fictional tech).
5. Sends the audio plus those vocab hints to Soniox for transcription.
6. Compiles the word-level timings into properly-paced SRT cues (max 42 chars per line, 2 lines, reading-speed checks, etc.).
7. Writes one `.srt` per input file to `out/`.

The vocab hints get passed to Soniox as `context.terms`. This is what lets it correctly transcribe things like "Coruscant" or character names that a generic ASR would butcher.

## Requirements

* Python 3.14+
* `ffmpeg` and `ffprobe` on your PATH
* Three API keys in a `.env` file:

```
SONIOX_API_KEY=...
TMDB_READ_ACCESS_TOKEN=...
ANTHROPIC_API_KEY=...
```

The TMDB token is the long "Read Access Token" from your TMDB API settings, not the short v3 API key.

## Install

```bash
git clone <repo>
cd subtitle-generator
uv sync
```

## Usage

Single file:

```bash
uv run main.py run --media-path "/path/to/Movie.2024.mkv"
```

Folder (recurses into subfolders):

```bash
uv run main.py run --media-path "/path/to/tv-shows/"
```

With language hints (helps Soniox stick to specific languages when audio is multilingual):

```bash
uv run main.py run --media-path "/path/to/foo.mkv" --language-hints en
```

With translation (Soniox transcribes the source language and translates the cues into the target):

```bash
uv run main.py run --media-path "/path/to/foo.mkv" --translation es
```

Output goes to `./out/` by default. Use `--output-dir` to change that.

## How failures are handled

Anything that goes wrong with a single file (bad ffmpeg, no TMDB match, Soniox timeout, unparseable filename) gets recorded and the rest of the batch keeps going. At the end you get a summary:

```
3 file(s) failed:
    - foo.mkv during stage [Audio Processing]: ffprobe failed: ...
    - bar.mkv during stage [File Parsing]: Cannot extract title from file.
    - baz.mkv during stage [Term Generation]: Claude response could not be parsed.
```

Process exits with code 1 if anything failed, so you can chain it into a script.

The thinking here: a four-hour batch shouldn't blow up because one file had a weird name. Skip it, log it, move on.

## Naming requirements

For best results, name files so `guessit` can parse them:

* `The.Matrix.1999.1080p.mkv`
* `Star.Wars.Andor.S01E01.mkv`
* `Andor S01E01.mkv`

If the title or season/episode can't be extracted, the file gets skipped (no context means no transcription, since the demo treats good vocab hints as a requirement). Rename and run again.

## Limits and known issues

* Soniox caps you at 100 pending transcriptions and 10GB of stored uploads at once. The batch runner respects both. If you have hundreds of files, the pipeline naturally throttles itself; you don't need to babysit it.
* Files over 5 hours (300 minutes) or 10GB are rejected outright. These are Soniox-side hard limits.
* Audio extraction picks the first stream tagged `eng`/`en`, or falls back to the first audio stream if everything is tagged `und`. If your file has only Italian or Japanese audio, point Soniox at it with `--language-hints` and skip the English autodetection assumption.
* 5.1/7.1 sources get the front-center channel extracted (where dialogue lives). Stereo and mono sources go through a standard ffmpeg downmix.
* Files in the same input tree should have unique filenames. `tv/s1/E01.mkv` and `tv/s2/E01.mkv` would both land at `temp/E01.flac` and the second would clobber the first.

## Project layout

```
main.py                 CLI entry, orchestrates the pipeline
process_audio.py        ffmpeg/ffprobe audio extraction
generate_context.py     TMDB + Claude vocab hints
batch.py                parallel Soniox transcription
generate_transcript.py  Soniox API wrapper (upload, poll, fetch)
compile_transcript.py   word tokens to cue partitioning
generate_srt.py         cues to SRT file
utils.py                FailureLog and Context types
```

Every stage takes a shared `FailureLog`, so a TMDB hiccup, an ffmpeg crash, and a Soniox timeout all end up in the same end-of-run summary.

## Why three different sources for vocab hints?

TMDB gives reliable cast and character names. Claude fills in everything TMDB doesn't track (places, organizations, fictional tech, slang). Soniox itself handles the actual transcription. Each does the thing it's best at.

You can disable either of the hint sources by failing their API keys (`TMDB_READ_ACCESS_TOKEN` or `ANTHROPIC_API_KEY`), but the demo treats both as required.
