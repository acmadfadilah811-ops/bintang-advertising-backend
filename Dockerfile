# Use official Python runtime as base image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies for MySQL client and other components
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy uv binary from official image for extremely fast dependency sync
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependency definition files
COPY pyproject.toml uv.lock ./

# Compile dependencies using uv and install directly to the system python environment
RUN uv pip compile pyproject.toml -o requirements.txt && \
    uv pip install --system --break-system-packages -r requirements.txt

# Copy project files
COPY . .

# Expose port (Cloud Run sets this dynamically, default to 8080)
EXPOSE 8080

# Run server using Daphne (ASGI) dynamically bound to the PORT environment variable
CMD ["sh", "-c", "daphne -b 0.0.0.0 -p ${PORT:-8080} core.asgi:application"]
