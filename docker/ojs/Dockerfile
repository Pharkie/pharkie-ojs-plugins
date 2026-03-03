FROM pkpofficial/ojs:3_5_0-3

# Add envsubst for config templating (PKP image is Debian-based)
USER root
RUN mkdir -p /var/lib/apt/lists/partial && apt-get update \
    && apt-get install -y --no-install-recommends gettext-base default-mysql-client jq \
    && rm -rf /var/lib/apt/lists/*
