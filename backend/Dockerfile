FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/piper \
    && curl -fsSL https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz -o /tmp/piper_linux_x86_64.tar.gz \
    && tar -xzf /tmp/piper_linux_x86_64.tar.gz -C /opt/piper \
    && rm -f /tmp/piper_linux_x86_64.tar.gz

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

COPY app ./app
COPY .env.example ./.env.example

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
