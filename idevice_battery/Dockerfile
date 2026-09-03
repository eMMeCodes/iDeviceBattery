# https://developers.home-assistant.io/docs/apps/configuration#app-dockerfile
ARG BUILD_ARCH=aarch64
ARG BUILD_FROM=ghcr.io/home-assistant/${BUILD_ARCH}-base-debian:bookworm
FROM ${BUILD_FROM}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    usbmuxd \
    libimobiledevice-utils \
    libplist-dev \
    build-essential \
    pkg-config \
    usbutils \
    python3 \
    python3-pip \
    python3-dev \
    libssl-dev \
    libffi-dev \
    curl \
    ca-certificates \
    avahi-daemon \
    avahi-utils \
    libnss-mdns \
    dbus \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --break-system-packages 'pymobiledevice3>=11.2.0' 'paho-mqtt>=2.0.0'

ARG UI_BUILD=0.9.25
RUN echo "ui ${UI_BUILD}" > /tmp/ui-build-id

COPY config.yaml /build/config.yaml
COPY devices_store.py pair_service.py webui.py rsd_battery.py mqtt_ha.py model.py product_map.json run.sh /
COPY www /www
RUN chmod a+x /run.sh /webui.py /rsd_battery.py /pair_service.py /devices_store.py /mqtt_ha.py /model.py

LABEL \
 org.opencontainers.image.title="Home Assistant App: iDevice Battery" \
 org.opencontainers.image.description="Pair iDevices over USB, poll battery over Wi-Fi, MQTT discovery for Home Assistant." \
 org.opencontainers.image.source="https://github.com/eMMeCodes/iDeviceBattery" \
 org.opencontainers.image.licenses="MIT"

CMD ["/run.sh"]
