FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so this layer is reused when only the code changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# The user is meant to be replaced with the one that owns the Syncthing folder
# (compose: `user: "1000:1000"`). The state directory is created for the default one.
RUN useradd --uid 1000 --no-create-home syncbaton \
    && mkdir /var/lib/syncbaton \
    && chown syncbaton /var/lib/syncbaton
USER syncbaton

EXPOSE 8790
VOLUME /var/lib/syncbaton
ENTRYPOINT ["syncbaton"]
CMD ["serve", "--config", "/etc/syncbaton.toml", "--state-dir", "/var/lib/syncbaton"]
