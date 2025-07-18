#!/bin/bash
# santral_install.sh
# Ubuntu 22.04 LTS üzerine profesyonel Asterisk + FreePBX kurulumu için tam otomatik script

set -e

echo "Dijital Santral Sistemi Kurulumu Başladı..."
sleep 2

# 1. Güncellemeler
apt update && apt upgrade -y

# 2. Temel Araçlar
apt install -y wget curl net-tools git vim nano unzip software-properties-common build-essential cron sox libncurses5-dev libssl-dev libxml2-dev libsqlite3-dev uuid-dev libjansson-dev libedit-dev

# 3. Apache, MariaDB, PHP
add-apt-repository ppa:ondrej/php -y
apt update
apt install -y apache2 mariadb-server php8.1 php8.1-{cli,common,curl,gd,mysql,mbstring,xml,bcmath,zip,fpm} libapache2-mod-php8.1

systemctl enable apache2
systemctl enable mariadb

# 4. MariaDB Güvenli Kurulum
mysql -e "UPDATE mysql.user SET Password=PASSWORD('santral123') WHERE User='root';"
mysql -e "DELETE FROM mysql.user WHERE User='';"
mysql -e "DROP DATABASE IF EXISTS test;"
mysql -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';"
mysql -e "FLUSH PRIVILEGES;"

# 5. Asterisk Kurulumu
cd /usr/src
wget http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-20-current.tar.gz
tar xvfz asterisk-20-current.tar.gz
cd asterisk-20.*
contrib/scripts/install_prereq install
./configure
make menuselect.makeopts
menuselect/menuselect --enable app_macro --enable res_http_websocket menuselect.makeopts
make -j$(nproc)
make install
make samples
make config
ldconfig

systemctl enable asterisk

# 6. FreePBX Kurulumu
cd /usr/src
wget https://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz
tar vxf freepbx-16.0-latest.tgz
cd freepbx

adduser asterisk --disabled-password --gecos "Asterisk User"
usermod -aG www-data asterisk
chown -R asterisk. /var/www/

systemctl stop apache2

./start_asterisk start
./install -n

# 7. Apache Ayar ve Hizmet Başlatma
chown -R asterisk. /var/www/
systemctl restart apache2
systemctl restart asterisk

# 8. UFW Güvenlik Duvarı Ayarları
ufw allow 22/tcp
ufw allow 80,443/tcp
ufw allow 5060,5061/udp
ufw allow 10000:20000/udp
ufw --force enable

# 9. Bilgilendirme
IP_ADDR=$(hostname -I | awk '{print $1}')
echo ""
echo "Kurulum Tamamlandı!"
echo "Web arayüze erişmek için: http://$IP_ADDR"
echo "Varsayılan root veritabanı şifresi: santral123"
echo "Lütfen web panelden ilk ayarları yapın."

exit 0
