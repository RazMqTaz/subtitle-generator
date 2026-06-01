import requests
import os

import guessit

import anthropic

from pathlib import Path

from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class MediaTerms(BaseModel):
    terms: list[str]


@dataclass
class Media:
    media_title: str | None
    media_type: str | None
    season: int | None
    episode: int | None


def parse_media(input_file: Path) -> Media:
    guessit_media = guessit.guessit(input_file.name)
    media_title = guessit_media.get("title", None)
    media_type = guessit_media.get("type", None)
    if media_type == "episode":
        media_type = "tv show"
    season = guessit_media.get("season", None)
    episode = guessit_media.get("episode", None)
    media = Media(
        media_title=media_title, media_type=media_type, season=season, episode=episode
    )
    return media


def fetch_characters(
    media_title: str, media_type: str, season: int | None, episode: int | None
) -> list[str]:
    """
    Queries TMDB using extracted media title.
    Sends GET request to TMDB for character list depending on media type.
    """

    characters: list[str] = []

    api_key = os.getenv("TMDB_READ_ACCESS_TOKEN")
    if not api_key:
        raise RuntimeError("Missing TMDB_READ_ACCESS_TOKEN")

    # Fetch TMDB id
    headers = {
        "Authorization": f"Bearer {api_key}",
        "accept": "application/json",
    }

    if media_type == "movie":
        tmdb_id = None
        search_response = requests.get(
            "https://api.themoviedb.org/3/search/movie",
            headers=headers,
            params={"query": media_title, "language": "en-US"},
            timeout=10,
        )
        search_response.raise_for_status()
        search_response = search_response.json()
        if search_response["results"]:
            tmdb_id = search_response["results"][0]["id"]

        if tmdb_id:
            credits_response = requests.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}/credits",
                headers=headers,
                params={"language": "en-US"},
                timeout=10,
            )
            credits_response.raise_for_status()
            credits = credits_response.json()
            if credits.get("cast", None):
                for member in credits["cast"]:
                    if member["character"]:
                        characters.append(member["character"])

    elif media_type == "tv show" and season and episode:
        tmdb_id = None
        search_response = requests.get(
            "https://api.themoviedb.org/3/search/tv",
            headers=headers,
            params={"query": media_title, "language": "en-US"},
            timeout=10,
        )
        search_response.raise_for_status()
        search_response = search_response.json()
        if search_response["results"]:
            tmdb_id = search_response["results"][0]["id"]
        if tmdb_id:
            credits_response = requests.get(
                f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}/credits",
                headers=headers,
                params={"language": "en-US"},
                timeout=10,
            )
            credits_response.raise_for_status()
            credits: dict = credits_response.json()
            if credits.get("cast", None):
                for member in credits["cast"]:
                    if member["character"]:
                        characters.append(member["character"])
    return characters


def fetch_media_terms(media_title: str, media_type: str) -> list[str]:
    """
    Queries Claude for unique terms to add to vocabulary.
    """
    client = _get_client()
    response = client.messages.parse(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=(
            "You generate vocabulary hints for a speech-to-text transcription model. "
            "Given a film or TV show, return proper nouns and invented terms that an "
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
                "content": f"{media_type}: {media_title}",
            }
        ],
        output_format=MediaTerms,
    )

    parsed = response.parsed_output
    if parsed is None:
        return []
    return parsed.terms  # list[str]


def _generate_context_file(
    input_file: Path,
) -> tuple[Path, dict[str, list[str]]]:
    """
    Given a file, orchestrates `fetch_characters` and `fetch_media_terms`.
    Returns `(input_file, context).
    """
    parsed_media = parse_media(input_file=input_file)
    if parsed_media.media_title and parsed_media.media_type:
        return (
            input_file,
            generate_context_title_type(
                title=parsed_media.media_title,
                media_type=parsed_media.media_type,
                season=parsed_media.season,
                episode=parsed_media.episode,
            ),
        )
    else:
        return (input_file, {"terms": []})


def generate_context(
    files: list[Path],
) -> dict[Path, dict[str, list[str]]]:
    """
    Given a list of files, use ThreadPoolExecutor to generate context per file.
    """
    context_map: dict[Path, dict[str, list[str]]] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for file, ctx in ex.map(_generate_context_file, files):
            context_map[file] = ctx
    return context_map


def generate_context_title_type(
    title: str, media_type: str, season: int | None, episode: int | None
) -> dict[str, list[str]]:
    """
    Given a `title`, orchestrates `fetch_characters` and `fetch_media_terms`.
    Returns context for Soniox.
    """
    context: dict[str, list[str]] = {"terms": []}
    terms: list[str] = []
    terms.extend(
        fetch_characters(
            media_title=title, media_type=media_type, season=season, episode=episode
        )
    )
    terms.extend(fetch_media_terms(media_title=title, media_type=media_type))
    # Remove dupes and return
    context["terms"] = list(dict.fromkeys(terms))
    return context
