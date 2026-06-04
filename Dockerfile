FROM python:3.14-slim-bookworm

# ffmpeg (provides ffprobe too)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv, copied from the official image (no pip install needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# dependency layer first
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# source last
COPY *.py ./

# non-root user, owns workdir
RUN useradd --create-home --shell /bin/bash subgen \
    && chown -R subgen:subgen /app

USER subgen

ENTRYPOINT ["uv", "run", "main.py"]