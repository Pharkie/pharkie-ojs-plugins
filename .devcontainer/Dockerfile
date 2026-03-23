FROM mcr.microsoft.com/devcontainers/base:ubuntu

# Node.js (for Claude Code + Playwright)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# PHP CLI (for plugin linting / WP-CLI if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
      php-cli php-xml php-mbstring php-curl \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI + Compose plugin (to manage the project's docker-compose stack)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
       | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
       docker-ce-cli docker-compose-plugin docker-buildx-plugin \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI + Python pip + DNS tools + socat (for port forwarding to compose services)
# poppler-utils: pdftotext/pdfinfo for journal backfill pipeline
# ocrmypdf + tesseract: OCR scanned archive PDFs for backfill
RUN apt-get update && apt-get install -y --no-install-recommends gh python3-pip pipx dnsutils socat poppler-utils sox \
      ocrmypdf tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --break-system-packages pyyaml grip pymupdf gspread "deprecation>=2.1.0"

# Ruby + anystyle (CRF-based bibliographic reference parser for citation classification)
RUN apt-get update && apt-get install -y --no-install-recommends ruby ruby-dev build-essential \
    && gem install --no-document anystyle anystyle-cli \
    && rm -rf /var/lib/apt/lists/*

# SOPS + age (for encrypting secrets in private repo)
RUN curl -Lo /usr/local/bin/sops https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.amd64 \
    && chmod +x /usr/local/bin/sops \
    && curl -Lo /tmp/age.tar.gz "https://dl.filippo.io/age/v1.2.1?for=linux/amd64" \
    && tar -xzf /tmp/age.tar.gz -C /tmp \
    && mv /tmp/age/age /tmp/age/age-keygen /usr/local/bin/ \
    && rm -rf /tmp/age /tmp/age.tar.gz

# Hetzner Cloud CLI (for managing staging/prod VPS)
RUN curl -sL https://github.com/hetznercloud/cli/releases/latest/download/hcloud-linux-amd64.tar.gz \
    | tar xz -C /usr/local/bin/

# Claude Code CLI — install as vscode user to avoid npm permission errors
USER vscode
RUN mkdir -p /home/vscode/.npm-global \
    && npm config set prefix /home/vscode/.npm-global \
    && npm install -g npm@latest \
    && npm install -g @anthropic-ai/claude-code
ENV PATH="/home/vscode/.npm-global/bin:${PATH}"
USER root

# Playwright system deps (for running e2e tests)
RUN npx playwright install-deps chromium 2>/dev/null || true
