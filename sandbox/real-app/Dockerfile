FROM ubuntu@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        fluxbox=1.3.7-1build2 \
        x11-apps=7.7+11build3 \
        x11-utils=7.7+6build2 \
        xdotool=1:3.20160805.1-5build1 \
        xvfb=2:21.1.12-1ubuntu1.6 \
        xterm=390-1ubuntu3 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
COPY --chmod=0555 entrypoint.sh capture-greeting.sh /usr/local/bin/
COPY --chmod=0444 fluxbox-init /usr/local/share/lotuspond-fluxbox-init
COPY --chmod=0444 fluxbox-style /usr/local/share/lotuspond-fluxbox-style

USER 10001:10001
ENV DISPLAY=:99 HOME=/home/app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
