# Build this generic image from the repository root:
# docker build -t sds200-daemon .
FROM python:3.14-slim AS build

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip wheel \
    --disable-pip-version-check \
    --wheel-dir /wheels \
    ".[mqtt]"

FROM python:3.14-slim

LABEL \
    org.opencontainers.image.title="sds200" \
    org.opencontainers.image.description="Uniden SDS200 network scanner daemon" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/stevenboyd78/sdsctl"

ENV \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/sdsctl \
    XDG_CONFIG_HOME=/config \
    XDG_STATE_HOME=/state \
    XDG_CACHE_HOME=/cache \
    XDG_RUNTIME_DIR=/run

COPY --from=build /wheels /wheels

RUN python -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        --no-index \
        --find-links=/wheels \
        "sds200[mqtt]" \
    && rm -rf /wheels \
    && groupadd --gid 10001 sdsctl \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --home-dir /home/sdsctl \
        --shell /usr/sbin/nologin \
        sdsctl \
    && mkdir -p /config /state /cache /run/sdsctl \
    && chown -R 10001:10001 \
        /config /state /cache /run/sdsctl /home/sdsctl

VOLUME ["/config", "/state", "/cache"]

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["sdsctl", "daemon-client", "status", "--json"]

USER 10001:10001

ENTRYPOINT ["sdsctl"]
CMD ["--help"]
