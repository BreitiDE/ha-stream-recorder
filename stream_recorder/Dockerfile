FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION=2.0.0
ARG BUILD_ARCH=amd64

# Python aus den Alpine-Paketen, Bibliotheken per pip (alle rein in Python, nichts zu kompilieren)
RUN apk add --no-cache python3 py3-pip tzdata
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY app /app
COPY run.sh /
RUN chmod a+x /run.sh

LABEL \
    io.hass.name="Stream Recorder" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="aarch64|amd64"

CMD [ "/run.sh" ]
