FROM ghcr.io/ggml-org/llama.cpp:server

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip curl ca-certificates \
    && pip3 install --no-cache-dir --break-system-packages pyyaml aiohttp cryptography aiortc \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
COPY app /yamlic
RUN chmod +x /run.sh
ENV PYTHONPATH=/yamlic

ENTRYPOINT []
CMD ["/run.sh"]
