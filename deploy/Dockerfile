# Hermes gateway image extended with the FinOps auditor and the CLIs its collectors call.
# Build from the repo root: docker compose -f deploy/docker-compose.yml build
# The base image is built from NousResearch/hermes-agent at the commit the Mac runs (see README).
ARG HERMES_IMAGE=hermes-agent:f7b26ef
FROM ${HERMES_IMAGE}

ARG DATABRICKS_CLI_VERSION=1.20.0
# Set by BuildKit (amd64 on the server).
ARG TARGETARCH

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq perl tzdata unzip gnupg ca-certificates curl \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-cloud-cli \
    && curl -fsSL --retry 3 -o /tmp/databricks.zip \
        "https://github.com/databricks/cli/releases/download/v${DATABRICKS_CLI_VERSION}/databricks_cli_${DATABRICKS_CLI_VERSION}_linux_${TARGETARCH}.zip" \
    && unzip -q /tmp/databricks.zip databricks -d /usr/local/bin \
    && rm -rf /tmp/databricks.zip /var/lib/apt/lists/*

COPY --chmod=a+rX,go-w . /opt/hermes-line
