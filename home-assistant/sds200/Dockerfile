# Build this monorepo image from the repository root:
# docker build -f home-assistant/sds200/Dockerfile .
FROM python:3.14-slim AS build

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip wheel \
    --disable-pip-version-check \
    --wheel-dir /wheels \
    ".[web,mqtt]"

FROM python:3.14-slim

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=unknown

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.source="https://github.com/stevenboyd78/sds200-python"

ENV \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=build /wheels /wheels

RUN python -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        --no-index \
        --find-links=/wheels \
        "sds200[web,mqtt]" \
    && rm -rf /wheels

CMD ["python", "-m", "sds200.home_assistant_app_supervisor"]
