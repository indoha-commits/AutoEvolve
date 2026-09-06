FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg make openssl \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN make setup-core setup-engines

EXPOSE 8787
CMD [".venv/bin/uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8787"]
