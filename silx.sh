#!/bin/bash
# FreePBX ve Asterisk Tam Temizleme Scripti
# Tüm paketler, dosyalar, veritabanları ve yapılandırmalar silinir

if [ "$(id -u)" -ne 0 ]; then
  echo "Bu script root olarak çalıştırılmalıdır!" >&2
  exit 1
fi

echo -e "\033[1;31mUYARI: Bu işlem GERİ ALINAMAZ!\033[0m"
echo -e "Sistemdeki tüm FreePBX, Asterisk ve bağlantılı bileşenler silinecek."
read -p "Devam etmek istiyor musunuz? (y/N): " confirm

if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
  echo "İşlem iptal edildi."
  exit 0
fi

echo -e "\n\033[1;33m[1/6] Servisler durduruluyor...\033[0m"
systemctl stop asterisk 2>/dev/null
systemctl stop apache2 2>/dev/null
systemctl stop mariadb 2>/dev/null
systemctl stop fail2ban 2>/dev/null
systemctl disable asterisk 2>/dev/null

echo -e "\n\033[1;33m[2/6] Paketler kaldırılıyor...\033[0m"
# Ana bileşenler
apt-get purge -y asterisk* freepbx* 2>/dev/null

# Bağımlılıklar
apt-get purge -y \
  apache2* php* mariadb* mysql* \
  libjansson-dev libsqlite3-dev libssl-dev \
  libncurses5-dev libxml2-dev libiksemel-dev \
  libcurl4-openssl-dev libical-dev libneon27-dev \
  libspandsp-dev libedit-dev libldap2-dev \
  libmemcached-dev libspeex-dev libspeexdsp-dev \
  libsrtp2-dev libpq-dev libnewt-dev libusb-dev \
  libpopt-dev liblua5.2-dev libopus-dev \
  libresample1-dev libvpb-dev sofia-sip-bin \
  unixodbc unixodbc-dev uuid uuid-dev \
  nodejs npm fail2ban sngrep 2>/dev/null

# Otomatik kaldırılan paketlerin bağımlılıkları
apt-get autoremove -y 2>/dev/null
apt-get clean 2>/dev/null

echo -e "\n\033[1;33m[3/6] Dosya ve dizinler siliniyor...\033[0m"
# FreePBX ve Asterisk dosyaları
rm -rf /var/lib/asterisk
rm -rf /var/spool/asterisk
rm -rf /var/log/asterisk
rm -rf /var/run/asterisk
rm -rf /etc/asterisk
rm -rf /usr/lib/asterisk
rm -rf /var/www/html/*
rm -rf /opt/freepbx_install

# Apache ve PHP
rm -rf /etc/apache2
rm -rf /var/www/*
rm -rf /etc/php

# Node.js ve PM2
rm -rf /usr/lib/node_modules
rm -rf /tmp/npm_cache
rm -rf /root/.npm
rm -rf /home/*/.npm
sudo -u asterisk pm2 unstartup 2>/dev/null

echo -e "\n\033[1;33m[4/6] Veritabanları siliniyor...\033[0m"
mysql -e "DROP DATABASE IF EXISTS asterisk;" 2>/dev/null
mysql -e "DROP USER IF EXISTS 'asteriskuser'@'localhost';" 2>/dev/null
mysql -e "FLUSH PRIVILEGES;" 2>/dev/null

echo -e "\n\033[1;33m[5/6] Kullanıcı ve gruplar kaldırılıyor...\033[0m"
userdel -r asterisk 2>/dev/null
groupdel asterisk 2>/dev/null

echo -e "\n\033[1;33m[6/6] Sistem ayarları temizleniyor...\033[0m"
# Firewall kuralları
ufw delete allow 80/tcp 2>/dev/null
ufw delete allow 443/tcp 2>/dev/null
ufw delete allow 5060/tcp 2>/dev/null
ufw delete allow 5060/udp 2>/dev/null
ufw delete allow 5061/tcp 2>/dev/null
ufw delete allow 10000:20000/udp 2>/dev/null

# Cron jobs
crontab -u asterisk -r 2>/dev/null

# Sistem optimizasyonlarını geri al
sed -i '/^net.ipv4.tcp_fin_timeout/d' /etc/sysctl.conf
sed -i '/^net.core.somaxconn/d' /etc/sysctl.conf
sed -i '/^vm.swappiness/d' /etc/sysctl.conf
sed -i '/^fs.file-max/d' /etc/sysctl.conf
sysctl -p 2>/dev/null

sed -i '/^asterisk/d' /etc/security/limits.conf
sed -i '/^\* soft nofile/d' /etc/security/limits.conf
sed -i '/^\* hard nofile/d' /etc/security/limits.conf

echo -e "\n\033[1;32mTemizleme işlemi tamamlandı!\033[0m"
echo -e "Sistemin tamamen temizlenmesi için yeniden başlatmanız önerilir."
