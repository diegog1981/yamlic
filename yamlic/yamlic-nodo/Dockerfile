FROM ghcr.io/ggml-org/llama.cpp:server

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
COPY app /yamlic_nodo
RUN chmod +x /run.sh

ENTRYPOINT []
CMD ["/run.sh"]
