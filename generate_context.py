import requests
import os
import guessit

from pathlib import Path


def fetch_characters(input_file: Path) -> list[str]:
    """
    Uses guessit to extract media title and type.
    Queries TMDB using extracted media title.
    Sends GET request to TMDB for character list depending on media type.
    """

    characters: list[str] = []

    api_key = os.getenv("TMDB_READ_ACCESS_TOKEN")
    if not api_key:
        raise RuntimeError("Missing TMDB_READ_ACCESS_TOKEN")

    # use guessit library to parse file for info
    guessit_media = guessit.guessit(input_file.name)
    media_title = guessit_media.get("title", None)
    media_type = guessit_media.get("type", None)

    if not media_type or not media_title:
        return characters

    # Fetch TMDB id
    headers = {
        "Authorization": f"Bearer {api_key}",
        "accept": "application/json",
    }
    search_response = requests.get(
        "https://api.themoviedb.org/3/search/multi",
        headers=headers,
        params={
            "query": media_title,
            "include_adult": "false",
            "language": "en-US",
            "page": 1,
        },
    )
    search_response.raise_for_status()
    tmdb_result = search_response.json()
    if tmdb_result.get("results"):
        tmdb_id = tmdb_result["results"][0].get("id")

        # if type is movie -> fetch first 8 characters
        if media_type == "movie":
            credits_response = requests.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}/credits",
                headers=headers,
                params={"language": "en-US"},
            )
            credits_response.raise_for_status()
            credits = credits_response.json()
            if credits.get("cast", None):
                for member in credits["cast"][:8]:
                    characters.append(member["character"])

        # if tv show -> fetch first 20 characters (contains all characters in the entire show)
        elif media_type == "episode":
            credits_response = requests.get(
                f"https://api.themoviedb.org/3/tv/{tmdb_id}/aggregate_credits",
                headers=headers,
                params={"language": "en-US"},
            )
            credits_response.raise_for_status()
            credits: dict = credits_response.json()
            if credits.get("cast", None):
                for member in credits["cast"][:20]:
                    if member["roles"]:
                        characters.append(member["roles"][0]["character"])
    return characters
