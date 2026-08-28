# Use a lightweight python image
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/data/cannabis_papers.db

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Fly uses 8080; Hugging Face Docker Spaces use 7860 via PORT.
ENV PORT=8080
ENV CHEAP_OPS=1
EXPOSE 8080 7860

# Configure entrypoint and startup command
ENTRYPOINT ["./entrypoint.sh"]
CMD ["sh", "-c", "exec gunicorn --workers 1 --timeout 180 --bind 0.0.0.0:${PORT:-8080} app:app"]
