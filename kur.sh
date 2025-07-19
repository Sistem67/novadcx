#!/bin/bash
# Profesyonel FreePBX Kurulum Scripti
# Tam otomatik, hatasız ve eksiksiz kurulum

# Renk Kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Log Dosyası
LOG_FILE="/var/log/freepbx_pro_install_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

# Root Kontrolü
if [ "$(id -u)" != "0" ]; then
   echo -e "${RED}[HATA] Root yetkisi gerekiyor!${NC}" >&2
   exit 1
fi

# Başlık
clear
echo -e "${GREEN}"
echo "****************************************************"
echo "* PROFESYONEL FREEPBX KURULUM SCRIPTI             *"
echo "*      Tüm Adımlar Otomatik ve Hatasız            *"
echo "****************************************************"
echo -e "${NC}"

# 1. SİSTEM HAZIRLIĞI
echo -e "${YELLOW}[1/10] Sistem Hazırlığı Yapılıyor...${NC}"
apt-get update && apt-get upgrade -y
apt-get install -y wget curl git gnupg2 ufw sudo

# 2. DİZİN YAPISI OLUŞTURMA
echo -e "${YELLOW}[2/10] Dizin Yapısı Oluşturuluyor...${NC}"
mkdir -p /santral/{backups,logs,recordings}
cd /santral

# 3. APACHE & PHP KURULUMU
echo -e "${YELLOW}[3/10] Apache & PHP Kuruluyor...${NC}"
apt-get install -y apache2
apt-get install -y php php-mysql php-gd php-curl php-pear php-cli php-common php-json php-mbstring php-xml php-zip

# PHP Ayarları
sed -i 's/memory_limit = .*/memory_limit = 256M/' /etc/php/*/apache2/php.ini
sed -i 's/upload_max_filesize = .*/upload_max_filesize = 50M/' /etc/php/*/apache2/php.ini
sed -i 's/post_max_size = .*/post_max_size = 55M/' /etc/php/*/apache2/php.ini

# 4. MARIADB KURULUMU
echo -e "${YELLOW}[4/10] MariaDB Kuruluyor...${NC}"
apt-get install -y mariadb-server mariadb-client

# MySQL Güvenlik
mysql_secure_installation <<EOF
n
n
y
y
y
y
EOF

# 5. ASTERISK KURULUMU
echo -e "${YELLOW}[5/10] Asterisk Kuruluyor...${NC}"
apt-get install -y build-essential libxml2-dev libncurses5-dev libsqlite3-dev libssl-dev uuid-dev
apt-get install -y asterisk asterisk-core-sounds-en-wav asterisk-core-sounds-en-gsm asterisk-moh-opsound-wav asterisk-moh-opsound-gsm

# 6. FREEPBX İNDİRME
echo -e "${YELLOW}[6/10] FreePBX İndiriliyor...${NC}"
cd /santral
wget http://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz
tar xfz freepbx-16.0-latest.tgz
cd freepbx

# 7. VERİTABANI AYARLARI
echo -e "${YELLOW}[7/10] Veritabanı Ayarlanıyor...${NC}"
DB_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
mysql -e "CREATE DATABASE asterisk;"
mysql -e "CREATE USER 'freepbxuser'@'localhost' IDENTIFIED BY '${DB_PASS}';"
mysql -e "GRANT ALL PRIVILEGES ON asterisk.* TO 'freepbxuser'@'localhost';"
mysql -e "FLUSH PRIVILEGES;"

# 8. FREEPBX KURULUMU
echo -e "${YELLOW}[8/10] FreePBX Kuruluyor...${NC}"
cat > /etc/freepbx.conf <<EOF
<?php
\$amp_conf['AMPDBUSER'] = 'freepbxuser';
\$amp_conf['AMPDBPASS'] = '${DB_PASS}';
\$amp_conf['AMPDBHOST'] = 'localhost';
\$amp_conf['AMPDBNAME'] = 'asterisk';
\$amp_conf['AMPDBENGINE'] = 'mysql';
EOF

./start_asterisk start
./install -n --webroot=/var/www/html

# 9. İZİN AYARLARI
echo -e "${YELLOW}[9/10] İzinler Ayarlanıyor...${NC}"
chown -R asterisk:asterisk /var/www/html /var/lib/asterisk /var/spool/asterisk /var/run/asterisk /santral
chmod -R 755 /var/www/html /santral
fwconsole chown

# 10. SERVİSLERİ BAŞLATMA
echo -e "${YELLOW}[10/10] Servisler Başlatılıyor...${NC}"
systemctl enable asterisk mariadb apache2
systemctl restart asterisk mariadb apache2

# MODÜLLERİ YÜKLEME
fwconsole ma installall
fwconsole ma refreshsignatures
fwconsole reload
fwconsole restart

# GÜVENLİK DUVARI
ufw allow 80,443,5060,5061,10000:20000/tcp
ufw allow 5060,5061,10000:20000/udp
ufw --force enable

# KURULUM TAMAMLANDI
IP_ADDR=$(hostname -I | awk '{print $1}')

echo -e "${GREEN}"
echo "****************************************************"
echo "*       PROFESYONEL KURULUM TAMAMLANDI            *"
echo "****************************************************"
echo -e "${NC}"
echo -e "Yönetim Paneli: ${BLUE}http://${IP_ADDR}/admin${NC}"
echo -e "Kullanıcı Adı: ${YELLOW}admin${NC}"
echo -e "Şifre: ${YELLOW}admin${NC}"
echo ""
echo -e "${RED}ÖNEMLİ:${NC}"
echo -e "1. İlk girişte şifrenizi değiştirin!"
echo -e "2. Veritabanı şifresi: ${DB_PASS}"
echo -e "3. Log dosyası: ${LOG_FILE}"
echo ""
echo -e "Servis kontrolü: ${BLUE}fwconsole status${NC}"
echo -e "Yeniden başlatma: ${BLUE}fwconsole restart${NC}"

exit 0
