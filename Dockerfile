# User container image
# Each user gets an isolated container running this image
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI (required by SDK subprocess)
RUN npm install -g @anthropic-ai/claude-code || pip install claude-agent-sdk

WORKDIR /app

# Copy application code
COPY main_server.py agent_server.py ./
COPY src/ src/

# Install Python dependencies
RUN pip install --no-cache-dir fastapi uvicorn python-multipart docker

# Create required directories
RUN mkdir -p /workspace/uploads /workspace/reports \
    /home/agent/.claude/shared-skills \
    /home/agent/.claude/personal-skills \
    /home/agent/.claude/sessions \
    /home/agent/.claude/memory \
    /hooks

EXPOSE 8000

# Default command: run agent_server (container-internal FastAPI)
CMD ["uvicorn", "agent_server:app", "--host", "0.0.0.0", "--port", "8000"]
