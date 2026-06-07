FROM python:3.12-slim AS ipdb-builder

RUN pip install --no-cache-dir duckdb==1.4.1

RUN mkdir -p /opt/fallback

COPY ipdb/build_fallback.py /tmp/build_fallback.py
RUN python /tmp/build_fallback.py --output /opt/fallback/ipt.db

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nftables \
    iproute2 \
    conntrack \
    sudo \
    curl \
    frr \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

COPY --from=ipdb-builder /opt/fallback/ipt.db /opt/fallback/ipt.db

RUN chmod +x scripts/start-ipt.sh

ENTRYPOINT [ "./scripts/start-ipt.sh" ]
