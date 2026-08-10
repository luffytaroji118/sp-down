FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    curl -fsSL https://deno.land/install.sh | DENO_DIR=/usr/local sh -s v2.0.0 && \
    ln -sf /usr/local/.deno/bin/deno /usr/local/bin/deno && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/downloads_temp

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
