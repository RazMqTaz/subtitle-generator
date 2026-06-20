import functools
import requests
import os
import guessit
import anthropic

from pathlib import Path
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from utils import FailureLog, Context, PROGRESS_COLUMNS
from rich.progress import Progress

from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()


class MediaTerms(BaseModel):
    """
    Schema used by Anthropic client.messages.parse(output_format=...) for structured output.
    """

    terms: list[str]


@dataclass
class Media:
    title: str
    media_type: str
    season: int | None
    episode: int | None


def parse_media(input_file: Path, failure_log: FailureLog) -> Media | None:
    guessit_media = guessit.guessit(input_file.name)
    media_title = guessit_media.get("title", None)
    if not media_title:
        failure_log.record(
            path=input_file,
            stage="[File Parsing]",
            error="Cannot extract title from file.",
        )
        return None
    media_type = guessit_media.get("type", None)
    if not media_type or (media_type not in ("movie", "episode")):
        failure_log.record(
            path=input_file,
            stage="[File Parsing]",
            error="Cannot extract media type from file.",
        )
        return None
    season = guessit_media.get("season", None)
    episode = guessit_media.get("episode", None)
    if media_type == "episode":
        if season is None:
            failure_log.record(
                path=input_file,
                stage="[File Parsing]",
                error="Could not extract season from file",
            )
            return None
        elif episode is None:
            failure_log.record(
                path=input_file,
                stage="[File Parsing]",
                error="Could not extract episode from file",
            )
            return None
        media_type = "tv show"
    media = Media(
        title=media_title, media_type=media_type, season=season, episode=episode
    )
    return media


def make_request(
    search_url: str, credits_url: str, media_title: str, headers: dict[str, str]
) -> list[str]:
    """
    Returns list of characters.
    """
    characters: list[str] = []
    search_response = requests.get(
        search_url,
        headers=headers,
        params={"query": media_title, "language": "en-US"},
        timeout=10,
    )
    search_response.raise_for_status()
    search_response = search_response.json()
    if search_response["results"]:
        tmdb_id = search_response["results"][0]["id"]

        credits_response = requests.get(
            credits_url.format(tmdb_id=tmdb_id),
            headers=headers,
            params={"language": "en-US"},
            timeout=10,
        )
        credits_response.raise_for_status()
        credits = credits_response.json()
        for member in credits.get("cast", []):
            if member["character"]:
                characters.append(member["character"])
    return characters


def fetch_characters(media: Media) -> list[str] | None:
    """
    Queries TMDB using extracted media title.
    Sends GET request to TMDB for character list depending on media type.
    """

    api_key = os.getenv("TMDB_READ_ACCESS_TOKEN")
    if not api_key:
        raise RuntimeError("Missing TMDB_READ_ACCESS_TOKEN")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "accept": "application/json",
    }

    if media.media_type == "movie":
        return make_request(
            search_url="https://api.themoviedb.org/3/search/movie",
            credits_url="https://api.themoviedb.org/3/movie/{tmdb_id}/credits",
            media_title=media.title,
            headers=headers,
        )

    elif (
        media.media_type == "tv show"
        and media.season is not None
        and media.episode is not None
    ):
        return make_request(
            search_url="https://api.themoviedb.org/3/search/tv",
            credits_url=f"https://api.themoviedb.org/3/tv/{{tmdb_id}}/season/{media.season}/episode/{media.episode}/credits",
            media_title=media.title,
            headers=headers,
        )
    return None


def fetch_media_terms(media: Media) -> list[str] | None:
    """
    Queries Claude for unique terms to add to vocabulary.
    """
    if media.media_type == "tv show":
        content = f"{media.media_type}: {media.title} S{media.season}E{media.episode}"
    else:
        content = f"{media.media_type}: {media.title}"

    response = client.messages.parse(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=(
            "You generate vocabulary hints for a speech-to-text transcription model. "
            "Given a film or specific TV show episode, return proper nouns and invented terms that an "
            "ASR model is likely to mis-transcribe because they are not common English "
            "words: place names, organizations/factions, fictional technology, "
            "species, and unique terminology specific to that title. "
            "Rules: spell each term exactly as it appears canonically; one entry per "
            "term, no duplicates; exclude common words, real-world generic terms, and character names; "
            "return at most 50 terms. If you don't recognize the title, return an empty list."
        ),
        messages=[
            {
                "role": "user",
                "content": content,
            }
        ],
        output_format=MediaTerms,
    )

    parsed = response.parsed_output
    if parsed is None:
        return None
    return parsed.terms


def _generate_context_file(
    input_file: Path, failure_log: FailureLog
) -> tuple[Path, Context | None]:
    """
    Given a file, orchestrates `fetch_characters` and `fetch_media_terms`.
    Returns `(input_file, context).
    """
    parsed_media = parse_media(input_file=input_file, failure_log=failure_log)
    if parsed_media is None:
        return (input_file, None)
    return (
        input_file,
        generate_context_title_type(
            media=parsed_media,
            failure_log=failure_log,
            file=input_file,
        ),
    )


def generate_context_title_type(
    media: Media,
    failure_log: FailureLog,
    file: Path,
) -> Context | None:
    """
    Given a `title`, orchestrates `fetch_characters` and `fetch_media_terms`.
    Returns context for Soniox.
    """
    context: Context = {"terms": []}

    with ThreadPoolExecutor(max_workers=2) as ex:
        characters_future = ex.submit(fetch_characters, media)
        terms_future = ex.submit(fetch_media_terms, media)

        try:
            characters = characters_future.result()
        except Exception as e:
            failure_log.record(
                path=file,
                stage="[Character Retrieval]",
                error=f"Character retrieval for file: {file} failed. Error: {e}",
            )
            return None

        try:
            terms = terms_future.result()
        except Exception as e:
            failure_log.record(
                path=file,
                stage="[Term Generation]",
                error=f"Term generation for file: {file} failed. Error: {e}",
            )
            return None

    if characters is None:
        failure_log.record(
            path=file,
            stage="[Character Retrieval]",
            error="Could not retrieve character list. (Most likely TMDB does not have character list for this).",
        )
        return None
    if terms is None:
        failure_log.record(
            path=file,
            stage="[Term Generation]",
            error="Claude's response could not be parsed.",
        )
        return None
    # Remove dupes and return
    context["terms"] = list(dict.fromkeys(characters + terms))
    return context


def generate_context(
    files: list[Path], failure_log: FailureLog
) -> tuple[list[Path], dict[Path, Context]]:
    """
    Given a list of files, use ThreadPoolExecutor to generate context per file in parallel.
    """
    context_map: dict[Path, Context] = {}
    survivors: list[Path] = []
    worker = functools.partial(_generate_context_file, failure_log=failure_log)
    with Progress(*PROGRESS_COLUMNS) as progress:
        task = progress.add_task("Generating context", total=len(files))
        with ThreadPoolExecutor(max_workers=8) as ex:
            for file, ctx in ex.map(worker, files):
                if ctx is not None:
                    survivors.append(file)
                    context_map[file] = ctx
                progress.advance(task)
    return (survivors, context_map)
