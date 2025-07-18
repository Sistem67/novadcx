#!/bin/bash

# Ubuntu için Tam Otomatik FreePBX Santral Kurulum/Kaldırma Scripti
# Versiyon: 3.0
# Tüm sistem temizliği yapar ve yeniden kurar

# Renk Tanımları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Log Dosyası
LOG_FILE="/var/log/freepbx_reinstall_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a ${LOG_FILE})
exec 2>&1

# Root Kontrolü
if [ "$(id -u)" != "0" ]; then
   echo -e "${RED}[HATA] Bu script root olarak çalıştırılmalıdır.${NC}" >&2
   exit 1
fi

# Başlık
clear
echo -e "${GREEN}"
echo "****************************************************"
echo "*      FreePBX TAM OTOMATİK SİL & YENİDEN KUR      *"
echo "*          Tüm Bileşenlerle Eksiksiz Kurulum       *"
echo "****************************************************"
echo -e "${NC}"
echo -e "${BLUE}Başlangıç Zamanı: $(date)${NC}"
echo -e "${YELLOW}Kurulum Logları: ${LOG_FILE}${NC}"

# 1. ÖNCEKİ KURULUMU TAMAMEN KALDIRMA
echo -e "${RED}[1/10] Önceki kurulum temizleniyor...${NC}"

# Servisleri durdur
systemctl stop asterisk mariadb apache2 > /dev/null 2>&1

# Tüm paketleri kaldır
apt-get purge -y asterisk* freepbx* mariadb* apache2* php* nodejs* > /dev/null 2>&1
apt-get autoremove -y > /dev/null 2>&1

# Dosyaları sil
rm -rf /etc/asterisk /var/lib/asterisk /var/www/html /usr/src/freepbx* /var/spool/asterisk
rm -rf /etc/freepbx.conf /etc/amportal.conf /tmp/freepbx* /root/.npm /var/lib/mysql

echo -e "${GREEN}==> Eski kurulum tamamen temizlendi.${NC}"

# 2. SİSTEM GÜNCELLEMELERİ
echo -e "${YELLOW}[2/10] Sistem güncellemeleri yapılıyor...${NC}"
apt-get update && apt-get upgrade -y
if [ $? -ne 0 ]; then
    echo -e "${RED}[HATA] Sistem güncellemeleri başarısız oldu!${NC}"
    exit 1
fi
echo -e "${GREEN}==> Sistem güncellemeleri tamamlandı.${NC}"

# 3. TEMEL PAKETLER
echo -e "${YELLOW}[3/10] Temel paketler kuruluyor...${NC}"
apt-get install -y wget curl git gnupg2 sudo net-tools ufw software-properties-common dirmngr
echo -e "${GREEN}==> Temel paketler kuruldu.${NC}"

# 4. APACHE & PHP
echo -e "${YELLOW}[4/10] Apache ve PHP kuruluyor...${NC}"
apt-get install -y apache2
apt-get install -y php php-mysql php-gd php-curl php-pear php-cli php-common php-json php-readline php-mbstring php-xml php-zip php-intl

# PHP Ayarları
sed -i 's/memory_limit = .*/memory_limit = 256M/' /etc/php/*/apache2/php.ini
sed -i 's/upload_max_filesize = .*/upload_max_filesize = 20M/' /etc/php/*/apache2/php.ini
sed -i 's/post_max_size = .*/post_max_size = 25M/' /etc/php/*/apache2/php.ini

systemctl restart apache2
echo -e "${GREEN}==> Apache ve PHP kurulumu tamamlandı.${NC}"

# 5. MARIADB KURULUMU
echo -e "${YELLOW}[5/10] MariaDB veritabanı kuruluyor...${NC}"
export DEBIAN_FRONTEND=noninteractive
debconf-set-selections <<< 'mariadb-server mariadb-server/root_password password temp_root_pass'
debconf-set-selections <<< 'mariadb-server mariadb-server/root_password_again password temp_root_pass'

apt-get install -y mariadb-server mariadb-client

# MySQL Güvenlik Ayarları
MYSQL_ROOT_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
mysql -uroot -ptemp_root_pass -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASS}';"
mysql -uroot -p${MYSQL_ROOT_PASS} -e "DELETE FROM mysql.user WHERE User='';"
mysql -uroot -p${MYSQL_ROOT_PASS} -e "DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');"
mysql -uroot -p${MYSQL_ROOT_PASS} -e "DROP DATABASE IF EXISTS test;"
mysql -uroot -p${MYSQL_ROOT_PASS} -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';"
mysql -uroot -p${MYSQL_ROOT_PASS} -e "FLUSH PRIVILEGES;"

# FreePBX için veritabanı oluşturma
MYSQL_FREEPBX_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
mysql -uroot -p${MYSQL_ROOT_PASS} <<EOF
CREATE DATABASE asterisk;
CREATE USER 'freepbxuser'@'localhost' IDENTIFIED BY '${MYSQL_FREEPBX_PASS}';
GRANT ALL PRIVILEGES ON asterisk.* TO 'freepbxuser'@'localhost';
FLUSH PRIVILEGES;
EOF

echo -e "${GREEN}==> MariaDB kurulumu ve yapılandırması tamamlandı.${NC}"

# 6. ASTERISK KURULUMU
echo -e "${YELLOW}[6/10] Asterisk kuruluyor...${NC}"
apt-get install -y build-essential libxml2-dev libncurses5-dev libsqlite3-dev libssl-dev uuid-dev
apt-get install -y asterisk asterisk-core-sounds-en-wav asterisk-core-sounds-en-gsm asterisk-moh-opsound-wav asterisk-moh-opsound-gsm
apt-get install -y asterisk-dahdi asterisk-dev asterisk-flite asterisk-voicemail

echo -e "${GREEN}==> Asterisk kurulumu tamamlandı.${NC}"

# 7. FREEPBX KURULUMU
echo -e "${YELLOW}[7/10] FreePBX kuruluyor...${NC}"
cd /usr/src
wget http://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz
tar xfz freepbx-16.0-latest.tgz
cd freepbx

# FreePBX yapılandırma dosyası oluştur
cat > /etc/freepbx.conf <<EOF
<?php
\$amp_conf['AMPDBUSER'] = 'freepbxuser';
\$amp_conf['AMPDBPASS'] = '${MYSQL_FREEPBX_PASS}';
\$amp_conf['AMPDBHOST'] = 'localhost';
\$amp_conf['AMPDBNAME'] = 'asterisk';
\$amp_conf['AMPDBENGINE'] = 'mysql';
EOF

./start_asterisk start
./install -n --webroot=/var/www/html

echo -e "${GREEN}==> FreePBX kurulumu tamamlandı.${NC}"

# 8. İZİN AYARLARI
echo -e "${YELLOW}[8/10] Dosya izinleri ayarlanıyor...${NC}"
chown -R asterisk:asterisk /var/www/html /var/lib/asterisk /var/spool/asterisk /var/run/asterisk
chmod -R 755 /var/www/html
fwconsole chown

echo -e "${GREEN}==> İzin ayarları tamamlandı.${NC}"

# 9. SERVİSLERİ BAŞLATMA
echo -e "${YELLOW}[9/10] Servisler başlatılıyor...${NC}"
systemctl enable asterisk mariadb apache2
systemctl restart asterisk mariadb apache2

# FreePBX modüllerini yükle
fwconsole ma refreshsignatures
fwconsole ma installall
fwconsole reload
fwconsole restart

echo -e "${GREEN}==> Servisler başlatıldı.${NC}"

# 10. GÜVENLİK AYARLARI
echo -e "${YELLOW}[10/10] Güvenlik ayarları yapılandırılıyor...${NC}"
# Firewall kuralları
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 5060/udp
ufw allow 5061/tcp
ufw allow 10000:20000/udp
ufw allow 22/tcp
ufw --force enable

# SSH güvenlik ayarları
sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd

echo -e "${GREEN}==> Güvenlik ayarları tamamlandı.${NC}"

# KURULUM SONU BİLGİLERİ
IP_ADDR=$(hostname -I | awk '{print $1}')

echo -e "${GREEN}"
echo "****************************************************"
echo "*                   KURULUM TAMAMLANDI             *"
echo "****************************************************"
echo -e "${NC}"
echo -e "FreePBX Yönetim Paneli: ${BLUE}http://${IP_ADDR}/admin${NC}"
echo -e "Varsayılan Kullanıcı: ${YELLOW}admin${NC}"
echo -e "Varsayılan Şifre: ${YELLOW}admin${NC}"
echo ""
echo -e "${RED}ÖNEMLİ GÜVENLİK UYARISI:${NC}"
echo -e "1. Hemen yönetim şifresini değiştirin!"
echo -e "2. MySQL root şifresi: ${MYSQL_ROOT_PASS}"
echo -e "3. FreePBX veritabanı şifresi: ${MYSQL_FREEPBX_PASS}"
echo ""
echo -e "Sistem durumunu kontrol etmek için: ${BLUE}fwconsole status${NC}"
echo -e "Servisleri yeniden başlatmak için: ${BLUE}fwconsole restart${NC}"
echo -e "Kurulum logları: ${BLUE}${LOG_FILE}${NC}"

# Şifreleri log dosyasından temizle
sed -i "/MYSQL_ROOT_PASS/d" ${LOG_FILE}
sed -i "/MYSQL_FREEPBX_PASS/d" ${LOG_FILE}

exit 0
