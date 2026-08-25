FROM apache/superset:6.1.0 AS runtime-base

USER root

# Superset 6.1.0 pins this version in its "postgres" optional dependency.
RUN . /app/.venv/bin/activate && \
    uv pip install --no-cache-dir \
      "psycopg2-binary==2.9.9" \
      "fastmcp==3.1.0"

COPY --chown=superset:superset superset_config.py /app/superset_config.py
ENV SUPERSET_CONFIG_PATH=/app/superset_config.py

USER superset

FROM runtime-base AS runtime

FROM runtime-base AS reports

USER root

# Superset 6.1 recommends Playwright with Chromium for Alerts & Reports.
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/local/share/playwright-browsers
RUN . /app/.venv/bin/activate && \
    uv pip install --no-cache-dir \
      "playwright>=1.37,<2" \
      "Pillow>=11,<13" \
      "openpyxl>=3.1,<4" && \
    playwright install-deps && \
    playwright install chromium

USER superset
