# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.

FROM python:3.12-slim

WORKDIR /app

# Install pinned dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install VB-OS Python SDK (not yet on PyPI — vendored as sdist)
COPY vendor/vb_os-0.1.0.tar.gz /tmp/
RUN pip install --no-cache-dir /tmp/vb_os-0.1.0.tar.gz && rm /tmp/vb_os-0.1.0.tar.gz

# Copy application
COPY bootstrap.py demo.py verify.py seed_studies.py seed_fhir.py ./
COPY boundaries/ boundaries/
COPY orthanc.json .

ENTRYPOINT ["python3"]
CMD ["demo.py", "--list"]
