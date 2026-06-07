FROM powerdns/pdns-recursor-53
USER root
RUN apt update && apt install -y iproute2 nftables sudo sed lua-http lua-cjson lua-posix jq curl iputils-ping bind9-dnsutils
COPY docker-entrypoint.sh /
#COPY doh-ipv4.txt /
RUN chmod +x /docker-entrypoint.sh & \
    sed -i 's/^%sudo\sALL=(ALL:ALL) ALL/%sudo   ALL=(ALL:ALL) NOPASSWD:ALL/g'  /etc/sudoers & \
    usermod -aG sudo pdns
USER pdns
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/docker-entrypoint.sh"]