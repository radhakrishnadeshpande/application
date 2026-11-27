FROM python:3.10-slim-bookworm

# CPU torch by default. For GPU build with:
#   docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu121 -t layout:table .
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir torch torchvision \
        --index-url ${TORCH_INDEX} --extra-index-url https://pypi.org/simple
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the OhMyTable weights at build time so the container starts offline
RUN python -c "from ohmytable import OhMyTable; OhMyTable(device='cpu')"

COPY app.py .
ENV TABLE_DEVICE=auto
EXPOSE 8890
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8890", "--workers", "1"]
