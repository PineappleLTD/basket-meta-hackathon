FROM node:24.22.0-bookworm-slim AS web-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN mkdir -p public && npm run build

FROM python:3.12.15-slim-bookworm AS python-build
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY news_engine/ ./news_engine/
COPY api/requirements.txt ./api/requirements.txt
COPY requirements-news.txt ./
RUN pip install --no-cache-dir -r requirements-news.txt

FROM python:3.12.15-slim-bookworm AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 libgomp1 tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 basket \
    && useradd --uid 1001 --gid basket --create-home basket \
    && mkdir /data && chown basket:basket /data
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    BASKET_DATA_DIR=/data \
    BASKET_RUNTIME_USER=basket \
    PORT=3000
COPY --from=web-build /usr/local/bin/node /usr/local/bin/node
COPY --from=python-build /opt/venv /opt/venv
COPY --chown=basket:basket api/ ./api/
COPY --chown=basket:basket news_engine/ ./news_engine/
COPY --chown=basket:basket scripts/ ./scripts/
COPY --from=web-build --chown=basket:basket /app/web/.next/standalone ./web/
COPY --from=web-build --chown=basket:basket /app/web/.next/static ./web/.next/static/
COPY --from=web-build --chown=basket:basket /app/web/public ./web/public/
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10m --retries=3 \
    CMD python -c 'import os, urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.environ.get("PORT", "3000") + "/api/health", timeout=4)'
ENTRYPOINT ["tini", "--"]
CMD ["python", "scripts/run_production.py"]
