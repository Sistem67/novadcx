#!/bin/bash

set -e

echo "Kurulum başlatılıyor. Lütfen bekleyin..."

# Hataları yok sayarak devam etmesi için:
trap 'echo "Bir hata oluştu ama kurulum devam ediyor..."' ERR

# Sistemi güncelle
apt update -y && apt upgrade -y

# Gereksinimler
apt install -y wget curl sudo gnupg2 ca-certificates lsb-release unzip git

# Hostname fix
hostnamectl set-hostname pbx
echo "127.0.0.1 pbx.localdomain pbx" >> /etc/hosts

# PHP 8.1 kurulumu
add-apt-repository ppa:ondrej/php -y
apt update -y
apt install -y php8.1 php8.1-{cli,curl,mysql,mbstring,xml,bcmath,gd,zip,ldap,xmlrpc,soap,intl}

# Apache ve MariaDB
apt install -y apache2 mariadb-server mariadb-client
systemctl enable apache2
systemctl enable mariadb

# Apache ayarı
a2enmod rewrite
systemctl restart apache2

# Node.js 14 kurulumu
curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
apt install -y nodejs

# Asterisk bağımlılıkları
apt install -y build-essential linux-headers-$(uname -r) \
subversion libjansson-dev libxml2-dev uuid-dev libsqlite3-dev \
libedit-dev libssl-dev libncurses5-dev uuid-dev libnewt-dev libusb-dev \
libiksemel-dev libpq-dev libcurl4-openssl-dev libspeex-dev libgsm1-dev \
libogg-dev libvorbis-dev libneon27-dev libtool-bin python3-dev

# DAHDI ve LibPRI (opsiyonel ama tavsiye edilir)
cd /usr/src
git clone -b next https://github.com/asterisk/dahdi-linux-complete.git dahdi
cd dahdi
make all && make install && make config

cd /usr/src
git clone https://github.com/asterisk/libpri.git
cd libpri
make && make install

# Asterisk kurulumu
cd /usr/src
wget http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-20-current.tar.gz
tar xvfz asterisk-20-current.tar.gz
cd asterisk-20*/

contrib/scripts/install_prereq install
./configure
make menuselect.makeopts
menuselect/menuselect --enable CORE-SOUNDS-EN-GSM --enable MOH-OPSOUND-WAV --enable app_macro menuselect.makeopts
make -j$(nproc)
make install
make samples
make config
ldconfig

# Asterisk user izinleri
useradd -m asterisk || true
chown -R asterisk. /var/run/asterisk /etc/asterisk /var/{lib,log,spool}/asterisk /usr/lib/asterisk
sed -i 's/^#AST_USER.*/AST_USER="asterisk"/' /etc/default/asterisk
sed -i 's/^#AST_GROUP.*/AST_GROUP="asterisk"/' /etc/default/asterisk

systemctl restart asterisk
systemctl enable asterisk

# FreePBX kurulumu
cd /usr/src
wget https://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz
tar xfz freepbx-16.0-latest.tgz
cd freepbx
./start_asterisk start
sleep 10
./install -n
fwconsole chown
fwconsole reload

# Apache yönlendirmesi
echo "<?php header('Location: /admin'); exit;" > /var/www/html/index.php
rm -f /var/www/html/index.html
systemctl restart apache2

# Admin şifresi
fwconsole unlock
fwconsole setadmin --username=admin --password=FreePBX2025!

# Bitir
echo "Kurulum tamamlandı!"
IP=$(hostname -I | awk '{print $1}')
echo "FreePBX arayüzü: http://$IP/admin"
echo "Kullanıcı: admin"
echo "Şifre: FreePBX2025!"
