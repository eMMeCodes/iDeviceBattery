ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm
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

RUN pip3 install --no-cache-dir --break-system-packages 'pymobiledevice3>=11.2.0'

COPY run.sh rsd_battery.py /
RUN chmod a+x /run.sh /rsd_battery.py

CMD ["/run.sh"]
