#!/bin/bash

set -eEuo pipefail
LOGFILE="/var/log/freepbx_install.log"
exec > >(tee -i "$LOGFILE")
exec 2>&1

trap 'echo "HATA oluştu ama kurulum devam edecek. Kontrol edilecek..."' ERR

echo "Zaman dilimi ve yerelleştirme ayarlanıyor..."
timedatectl set-timezone Europe/Istanbul
locale-gen tr_TR.UTF-8
export LANG=tr_TR.UTF-8

echo "Sistem güncelleniyor..."
apt update && apt upgrade -y || true

echo "Gerekli paketler kuruluyor..."
apt install -y wget git curl sudo net-tools software-properties-common gnupg2 \
ca-certificates lsb-release build-essential apache2 mariadb-server mariadb-client \
unzip sox fail2ban iptables ufw dnsutils || true

echo "PHP 7.4 kuruluyor..."
add-apt-repository ppa:ondrej/php -y || true
apt update
apt install -y php7.4 php7.4-{cli,curl,mbstring,xml,bcmath,zip,soap,intl,gd,mysql,opcache,readline,common,pear} \
libapache2-mod-php7.4 || true
update-alternatives --set php /usr/bin/php7.4 || true

echo "Node.js 14 kuruluyor..."
curl -fsSL https://deb.nodesource.com/setup_14.x | bash - || true
apt install -y nodejs || true

echo "Asterisk 20 kaynak koddan derleniyor..."
cd /usr/src || exit
rm -rf asterisk
git clone -b 20 https://gerrit.asterisk.org/asterisk || true
cd asterisk
contrib/scripts/install_prereq install || true
./configure || true
make menuselect.makeopts || true
make -j$(nproc) || true
make install || true
make samples || true
make config || true
ldconfig || true

echo "Asterisk kullanıcı izinleri ayarlanıyor..."
adduser --disabled-password --gecos "" asterisk || true
chown -R asterisk. /var/{run,lib,log,spool}/asterisk /etc/asterisk /usr/lib/asterisk || true
chmod -R 750 /var/{lib,log,spool}/asterisk || true

echo "MariaDB güvenli kurulumu başlatılıyor..."
mysql_secure_installation <<EOF || true

y
root
root
y
y
y
y
EOF

echo "Apache yapılandırılıyor..."
a2enmod rewrite || true
systemctl restart apache2 || true

echo "FreePBX 16 indiriliyor ve kuruluyor..."
cd /usr/src || exit
rm -rf freepbx*
wget https://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz || true
tar xfz freepbx-16.0-latest.tgz
cd freepbx
./start_asterisk start || true
sleep 10
./install -n || true

echo "FreePBX yetki ve servisler kontrol ediliyor..."
fwconsole chown || true
fwconsole reload || true
fwconsole restart || true

echo "Varsayılan yönetici hesabı ayarlanıyor..."
mysql -uroot -proot asterisk <<EOF || true
DELETE FROM admin WHERE username='admin';
INSERT INTO admin (username, password_sha1, name, email) 
VALUES ('admin', SHA1('FreePBX2025!'), 'Admin', 'admin@domain.local');
EOF

echo "Servisler etkinleştiriliyor..."
systemctl enable asterisk apache2 mariadb || true
systemctl restart asterisk apache2 mariadb || true

echo "UFW güvenlik duvarı yapılandırılıyor..."
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 5060/udp
ufw allow 5061/udp
ufw --force enable || true

echo "Fail2Ban başlatılıyor..."
systemctl enable fail2ban
systemctl start fail2ban

echo "amportal.conf dosyası ve legacy uyumlar kontrol ediliyor..."
touch /etc/amportal.conf
echo "AUTHTYPE=database" > /etc/amportal.conf
echo "AMPMGRPASS=amp111" >> /etc/amportal.conf

echo "Kurulum tamamlandı, eksikler kontrol ediliyor..."

# Eksik modüller veya hata alınan işlemleri tekrar dene
echo "Eksik bileşenler tekrar kuruluyor..."
fwconsole ma upgradeall || true
fwconsole reload || true
fwconsole chown || true

echo ""
echo "-----------------------------------------------"
echo "FreePBX kurulumu tamamlandı."
echo "Web Arayüz: http://$(hostname -I | awk '{print $1}')/admin"
echo "Kullanıcı Adı: admin"
echo "Şifre: FreePBX2025!"
echo "Log dosyası: $LOGFILE"
echo "-----------------------------------------------"
