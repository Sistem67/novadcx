#!/bin/bash

set -e

echo "FreePBX + Asterisk kurulumu başlatılıyor..."

trap 'echo "Bir hata oluştu ama kurulum devam ediyor..."' ERR

apt update -y && apt upgrade -y

apt install -y wget curl git sudo lsb-release software-properties-common

hostnamectl set-hostname pbx
echo "127.0.0.1 pbx.localdomain pbx" >> /etc/hosts

add-apt-repository ppa:ondrej/php -y
apt update -y
apt install -y php8.1 php8.1-{cli,curl,mysql,mbstring,xml,bcmath,gd,zip,ldap,xmlrpc,soap,intl}

apt install -y apache2 mariadb-server mariadb-client
systemctl enable apache2
systemctl enable mariadb

a2enmod rewrite
systemctl restart apache2

curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
apt install -y nodejs

apt install -y build-essential linux-headers-$(uname -r) subversion \
libjansson-dev libxml2-dev uuid-dev libsqlite3-dev libedit-dev \
libssl-dev libncurses5-dev libnewt-dev libusb-dev libcurl4-openssl-dev \
libspeex-dev libgsm1-dev libogg-dev libvorbis-dev libtool-bin python3-dev

cd /usr/src
wget http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-20-current.tar.gz
tar xvfz asterisk-20-current.tar.gz
cd asterisk-20*/

contrib/scripts/install_prereq install
./configure
make menuselect.makeopts
menuselect/menuselect --disable chan_dahdi --enable app_macro --enable res_http_websocket menuselect.makeopts

make -j$(nproc)
make install
make samples
make config
ldconfig

useradd -m asterisk || true
chown -R asterisk. /var/run/asterisk /etc/asterisk /var/{lib,log,spool}/asterisk /usr/lib/asterisk
sed -i 's/^#AST_USER.*/AST_USER="asterisk"/' /etc/default/asterisk
sed -i 's/^#AST_GROUP.*/AST_GROUP="asterisk"/' /etc/default/asterisk

systemctl restart asterisk
systemctl enable asterisk

cd /usr/src
wget https://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz
tar xfz freepbx-16.0-latest.tgz
cd freepbx
./start_asterisk start
sleep 10
./install -n
fwconsole chown
fwconsole reload

echo "<?php header('Location: /admin'); exit;" > /var/www/html/index.php
rm -f /var/www/html/index.html
systemctl restart apache2

fwconsole unlock
fwconsole setadmin --username=admin --password=FreePBX2025!

IP=$(hostname -I | awk '{print $1}')
echo ""
echo "Kurulum tamamlandı"
echo "Web arayüz: http://$IP/admin"
echo "Kullanıcı adı: admin"
echo "Şifre: FreePBX2025!"
