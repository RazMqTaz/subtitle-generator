# Subtitle Generator

Generates SRT subtitle files for video using Soniox's async speech-to-text. Built as a demo of what the Soniox API can do when you wrap it with some practical scaffolding (audio extraction, vocabulary hints, cue timing).

Point it at a video file or a folder of videos and it spits out SRT files.

> This is the **`frontend`** branch. It includes everything on `main` plus an optional browser-based UI for demos. The CLI workflow is unchanged. If you only need the CLI, the `main` branch is leaner.

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

## Install: bare-metal (alternative)

If you'd rather skip Docker, you need Python 3.14+, `uv`, and `ffmpeg`/`ffprobe` on your PATH.

```bash
git clone <repo>
cd subtitle-generator
uv sync
```

Output and temp directories default to `./out` and `./temp` in the project root.

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

### Where the files have to live

The Docker container only sees what's bind-mounted. By default `compose.yaml` mounts your home directory read-only at the same path inside the container, so any path under `~` works as-is. If you want to process files outside your home (an external drive, `/mnt/...`, etc.), edit the `volumes:` block in `compose.yaml` to mount that path.

The Docker output path `/out` is mapped to `./out` in the project root. SRT files land there on your host after each run.

### Optional: shell alias for less typing

Add this to `~/.bashrc` (or your shell's equivalent) so you can run from anywhere:

```bash
alias subgen='docker compose -f /path/to/subtitle-generator/compose.yaml run --rm subgen run --output-dir /out --media-path'
```

Then:

```bash
subgen ~/Videos/foo.mkv
subgen ~/Videos/tv-shows/
```

## Web UI (demo)

A small browser frontend wraps the pipeline for public demos. It accepts the three API keys, takes file uploads, runs the pipeline as a subprocess on the server, and serves the resulting SRT files back as downloads. The CLI is unaffected.

### Run locally

```bash
uv sync --extra web
ALLOWED_ORIGIN='*' uv run uvicorn server:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000` in a browser. Enter the keys, drop in one or more video files, and click "Generate subtitles." The page polls every two seconds for progress and shows download links when each job finishes.

`ALLOWED_ORIGIN='*'` disables the origin check for local development. Use the actual deployment URL in production.

### Deploy

The server is designed to run behind a TLS-terminating reverse proxy (Railway, Caddy, nginx, Cloudflare, etc.). Production checklist:

1. **Set `ALLOWED_ORIGIN`** to the exact origin users will hit the demo on, e.g. `https://demo.soniox.com`. The server refuses to start without it.
2. **Pin the Tailwind version and compute its SRI hash.** Pick a version, then run:
   ```bash
   curl -sL https://unpkg.com/@tailwindcss/browser@VERSION \
     | openssl dgst -sha384 -binary | openssl base64 -A
   ```
   Replace `@VERSION` and the placeholder `integrity="sha384-…"` value in `templates/index.html`. The browser will refuse to execute the script if its hash ever changes, defending against a compromised CDN.
3. **Make sure no `.env` file exists in the production container** and that no `SONIOX_API_KEY` / `TMDB_READ_ACCESS_TOKEN` / `ANTHROPIC_API_KEY` env vars are set at the platform level. Any of those would silently override the per-request keys. Users must supply their own keys via the form.
4. **Verify HTTPS is enforced** at the proxy layer (Railway gives you this by default).

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ALLOWED_ORIGIN` | (required) | Exact origin the browser must send on POSTs. Use `*` only for local dev. |
| `JOB_TTL_HOURS` | `24` | How long job artifacts live on disk before the sweeper deletes them. |
| `SWEEP_INTERVAL_SECONDS` | `3600` | How often the sweeper runs. |

### What the server does and doesn't do

* **Each job runs as a subprocess.** Keys are passed via the subprocess environment, used for that one job, and then dropped. They are never written to disk, never logged, and never echoed in the events shown to the client.
* **A strict Content Security Policy** is set on every response. `connect-src 'self'` is the key line: even if an attacker managed to run a script on the page, the browser would refuse to send the keys to any origin except ours.
* **Origin / Referer is checked** on every state-changing request, blocking cross-site form submissions.
* **Job artifacts auto-delete.** The browser fires a cleanup request via `navigator.sendBeacon` when the user closes or refreshes the page. A daemon sweep also runs hourly and removes any directory older than `JOB_TTL_HOURS`.
* **Subprocess output is not returned to the client.** Only a whitelist of recognized progress lines (parsed server-side into clean events) is shown. ffmpeg output, Python tracebacks, and anything unrecognized goes only to the server's own stderr.
* **No client-side key persistence.** Keys live in the form fields until submit, then are gone. No `localStorage`, no `sessionStorage`. `autocomplete="new-password"` is set so browsers don't offer to save them.

### What's deliberately not in scope

These belong to the deployment, not the app:

* **TLS / HSTS enforcement.** Done at the reverse proxy. The `Strict-Transport-Security` header is sent regardless.
* **DDoS mitigation, WAF rules.** Use Cloudflare or the platform's edge.
* **Rate limiting.** Users are billed against their own Soniox quota; abuse of the demo box itself would be an ops concern handled at the proxy or platform layer.
* **Auth.** This is a public, key-bring-your-own demo. There is intentionally no login.

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

server.py               FastAPI server for the web UI demo
templates/index.html    Web UI page
static/app.js           Web UI logic
```

Every stage takes a shared `FailureLog`, so a TMDB hiccup, an ffmpeg crash, and a Soniox timeout all end up in the same end-of-run summary.

## Why three different sources for vocab hints?

TMDB gives reliable cast and character names. Claude fills in everything TMDB doesn't track (places, organizations, fictional tech, slang). Soniox itself handles the actual transcription. Each does the thing it's best at.

You can disable either of the hint sources by failing their API keys (`TMDB_READ_ACCESS_TOKEN` or `ANTHROPIC_API_KEY`), but the demo treats both as required.
