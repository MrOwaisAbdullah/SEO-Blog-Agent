FROM python:3.13-slim

# Install UV from official source (no pip needed for UV itself)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Set up non-root user for Hugging Face Spaces (user ID 1000)
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

# Set working directory
WORKDIR /home/user/app

# Copy and install dependencies
COPY pyproject.toml ./
# Sync dependencies with UV (installs into .venv)
RUN uv sync 

# Copy the rest of the project
COPY . .

# Expose Hugging Face's default port
EXPOSE 7860

# Run the app with Uvicorn for production (adjust --workers based on CPU)
CMD ["/home/user/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "2"]