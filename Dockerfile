# Use a lightweight python image
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/app/data/cannabis_papers.db

# Install system dependencies (curl for health; awscli comes from pip in start/build)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt awscli

# Copy project files
COPY . .

# Make start scripts executable
RUN chmod +x entrypoint.sh scripts/start_web.sh

EXPOSE 8080

# Production start: SQLite from R2, never Fly Postgres.
CMD ["./scripts/start_web.sh"]
