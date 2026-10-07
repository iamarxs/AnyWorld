FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md app.py config.example.yaml ./
COPY api ./api
COPY core ./core
COPY logic ./logic
COPY static ./static
COPY templates ./templates
RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 anyworld \
    && useradd --uid 10001 --gid 10001 --no-create-home anyworld \
    && mkdir -p /app/.logged_games /app/.debug /app/certs \
    && chown -R 10001:10001 /app/.logged_games /app/.debug /app/certs
USER 10001:10001
EXPOSE 4141
ENTRYPOINT ["anyworld"]
