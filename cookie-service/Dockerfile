FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        fonts-liberation \
        libnss3 \
        libatk-bridge2.0-0 \
        libdrm2 \
        libxkbcommon0 \
        libgbm1 \
        libasound2 \
        libxshmfence1 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libxss1 \
        libxcursor1 \
        libxinerama1 \
        libpangocairo-1.0-0 \
        libgtk-3-0 \
        libcairo2 \
        libpango-1.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libexpat1 \
        libgdk-pixbuf-2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV PORT=8001

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .

EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
