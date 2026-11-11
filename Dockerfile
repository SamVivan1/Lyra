FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only what building the package needs. Naming the files keeps .env,
# .venv, .git and data/ out of the image even if .dockerignore drifts.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

CMD ["python", "-m", "lyra", "--loop"]
