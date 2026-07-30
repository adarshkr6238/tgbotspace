# Trigger rebuild 4
FROM python:3.11-slim

# Install FFmpeg and build dependencies for tgcrypto
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    python3-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces recommended non-root user
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Remove build dependencies to keep image small
USER root
RUN apt-get purge -y --auto-remove gcc python3-dev libssl-dev
USER user

COPY --chown=user . .

# Ensure storage directories exist and are writable
RUN mkdir -p downloads work_temp && chmod 777 downloads work_temp

CMD ["python", "main.py"]
