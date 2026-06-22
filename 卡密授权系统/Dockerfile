FROM python:3.11-slim

WORKDIR /app

# Install lightweight dependencies
RUN pip install --no-cache-dir fastapi uvicorn pydantic rsa

# Copy application files
COPY 云端鉴权服务端.py /app/
COPY static /app/static/

# Expose port
EXPOSE 8000

# Set environment variable (can be overridden during run)
ENV LICENSE_PRIVATE_KEY=""

# Start FastAPI server
CMD ["uvicorn", "云端鉴权服务端:app", "--host", "0.0.0.0", "--port", "8000"]
