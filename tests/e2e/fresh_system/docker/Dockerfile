# A fresh Ubuntu 24.04 system with systemd (user sessions via linger), one
# ordinary user "tester" and ONLY git + curl — what the README promises is
# enough. Nothing from the dev machine.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus dbus-user-session git curl ca-certificates \
      locales sudo iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8 \
    && userdel -r ubuntu && useradd -m -u 1000 -s /bin/bash tester \
    && echo "tester ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/tester \
    && mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/tester \
    && systemctl mask systemd-udevd.service systemd-udevd-kernel.socket systemd-udevd-control.socket \
         systemd-modules-load.service sys-kernel-config.mount sys-kernel-debug.mount \
         systemd-networkd.service systemd-resolved.service
ENV LANG=en_US.UTF-8
STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd", "--system", "--log-level=warning"]
