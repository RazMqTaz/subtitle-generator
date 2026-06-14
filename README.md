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

## API keys

Three keys go in a `.env` file in the project root:

```
SONIOX_API_KEY=...
TMDB_READ_ACCESS_TOKEN=...
ANTHROPIC_API_KEY=...
```

The TMDB token is the long "Read Access Token" from your TMDB API settings, not the short v3 API key.

## Install: Docker (recommended)

The Docker setup handles Python, ffmpeg, and dependencies for you. You only need Docker on your host.

```bash
git clone <repo>
cd subtitle-generator
# create .env (see above)
docker compose build
```

The first build takes a couple of minutes. After that you're set.

If `docker compose` complains about permissions, add yourself to the `docker` group once and you won't need `sudo`:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

## Run without the repo (just the published image)

If you don't want to clone or build anything, pull the prebuilt image from Docker Hub. It's multi-arch, so Docker grabs the right build for your CPU (Intel or ARM) automatically — there's no architecture to pick.

You need just two files in a folder:

**`compose.yaml`**

```yaml
services:
  subgen:
    image: razmqtaz/subgen:latest
    env_file: .env
    environment:
      SUBGEN_TEMP_DIR: /tmp/subgen
    tmpfs:
      - /tmp/subgen:size=8g
    volumes:
      - ${HOME}:${HOME}              # read-write so SRTs can land next to your videos
      - ./out:/out                   # only used if you pass --output-dir /out
```

**`.env`** (see [API keys](#api-keys) above)

```
SONIOX_API_KEY=...
TMDB_READ_ACCESS_TOKEN=...
ANTHROPIC_API_KEY=...
```

Then run — the first run auto-pulls the image:

```bash
docker compose run --rm subgen run --media-path ~/Videos/Show
```

Media has to live under your home directory (mounted read-write). By default each `.srt` is written next to its source video — what Jellyfin/Plex want; pass `--output-dir ~/Subtitles` (any path under your home) to collect them in one folder instead. A ready-made copy of this compose file ships in the repo as `compose.pull.yaml`. No Python, ffmpeg, or repo clone required — just Docker and your keys.

## Install: bare-metal (alternative)

If you'd rather skip Docker, you need Python 3.14+, `uv`, and `ffmpeg`/`ffprobe` on your PATH.

```bash
git clone <repo>
cd subtitle-generator
uv sync
```

The temp directory defaults to `./temp`. By default each `.srt` is written next to its source video; pass `--output-dir` to collect them all in one folder instead (see [Where the files go](#where-the-files-go)).

## Usage

The examples below show both runtime styles. Pick the one that matches your install.

Single file:

```bash
# Docker
docker compose run --rm subgen run --media-path ~/Videos/Movie.2024.mkv --output-dir /out

# Bare-metal
uv run main.py run --media-path ~/Videos/Movie.2024.mkv
```

Folder (recurses into subfolders):

```bash
# Docker
docker compose run --rm subgen run --media-path ~/Videos/tv-shows --output-dir /out

# Bare-metal
uv run main.py run --media-path ~/Videos/tv-shows
```

With language hints (helps Soniox stick to specific languages when audio is multilingual):

```bash
uv run main.py run --media-path ~/Videos/foo.mkv --language-hints en
```

With translation (Soniox transcribes the source language and translates the cues into the target):

```bash
uv run main.py run --media-path ~/Videos/foo.mkv --translation es
```

With language filter (drops tokens Soniox tags as anything other than the chosen language; useful for content with made-up alien languages):

```bash
uv run main.py run --media-path ~/Videos/foo.mkv --keep-only en
```

Mark the subtitle as the default track (adds `.default` to the filename so Jellyfin/Plex pre-select it):

```bash
uv run main.py run --media-path ~/Videos/foo.mkv --default
```

The `.srt` filename is also tagged with the language code when one is known (from `--keep-only`, `--translation`, or `--language-hints`), so a run with `--keep-only en --default` produces `Movie.2024.default.en.srt`.

### Where the files go

The Docker container only sees what's bind-mounted. `compose.yaml` mounts your home directory at the same path inside the container, so any path under `~` works as-is — for both input and output. It's mounted **read-write** so the tool can write SRTs back next to your videos. To use files outside your home (an external drive, `/mnt/...`, etc.), add that path to the `volumes:` block.

**Output location** depends on whether you pass `--output-dir`:

* **Omit it** (default): each `.srt` is written **next to its source video**, with a matching filename — this is what Jellyfin/Plex want for automatic external-subtitle pickup. (Relies on the read-write home mount above.)
* **`--output-dir <folder>`**: every `.srt` is dumped flat into one folder.

**Setting a custom output folder in Docker.** Because your home is mounted read-write at the same path, just point `--output-dir` at any absolute path under your home and the files land there on the host directly — no compose editing needed:

```bash
docker compose run --rm subgen run \
  --media-path ~/Videos/Show \
  --output-dir ~/Subtitles      # SRTs appear in ~/Subtitles on your host
```

As a convenience the repo's `compose.yaml` also maps `/out` → `./out` (project root), so `--output-dir /out` drops everything in `./out`; remap the left side of that `./out:/out` volume to relocate it. But for most cases an `--output-dir` under `~` is simpler. (A folder outside `~` only works if you've added a volume for it.)

### Optional: shell alias for less typing

Add this to `~/.bashrc` (or your shell's equivalent) so you can run from anywhere:

```bash
alias subgen='docker compose -f /path/to/subtitle-generator/compose.yaml run --rm subgen run --media-path'
```

Then:

```bash
subgen ~/Videos/foo.mkv
subgen ~/Videos/tv-shows/
```

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
Dockerfile              image definition (Python + ffmpeg + uv)
compose.yaml            local dev compose (builds from source)
compose.pull.yaml       end-user compose (pulls the published image)
```

Every stage takes a shared `FailureLog`, so a TMDB hiccup, an ffmpeg crash, and a Soniox timeout all end up in the same end-of-run summary.

## Why three different sources for vocab hints?

TMDB gives reliable cast and character names. Claude fills in everything TMDB doesn't track (places, organizations, fictional tech, slang). Soniox itself handles the actual transcription. Each does the thing it's best at.

You can disable either of the hint sources by failing their API keys (`TMDB_READ_ACCESS_TOKEN` or `ANTHROPIC_API_KEY`), but the demo treats both as required.
