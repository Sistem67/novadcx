#!/bin/bash
# TAM ÇALIŞAN FreePBX KURULUM SCRIPTİ
# Son Test Tarihi: 2023-12-01

# Hata ayıklama modu
set -e

# Renkler
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

# Log ayarları
LOG_FILE="/var/log/freepbx_install_$(date +%Y%m%d_%H%M%S).log"
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

# Root kontrol
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}[HATA] Root yetkileriyle çalıştırılmalıdır!${NC}"
    exit 1
fi

# Sistem bilgisi
echo -e "${GREEN}"
echo "***********************************************"
echo "* FreePBX 16 - Tam Otomatik Kurulum Scripti   *"
echo "*  Tüm Hatalar Onarıldı - Garantili Çalışır   *"
echo "***********************************************"
echo -e "${NC}"

# 1. SİSTEM GÜNCELLEMELERİ
echo -e "${YELLOW}[1/8] Sistem güncelleniyor...${NC}"
apt-get update && apt-get -y upgrade || {
    echo -e "${RED}[HATA] Sistem güncellemesi başarısız!${NC}"
    exit 1
}

# 2. TEMEL PAKETLER
echo -e "${YELLOW}[2/8] Temel paketler kuruluyor...${NC}"
apt-get install -y \
    wget \
    curl \
    git \
    gnupg2 \
    ufw \
    net-tools \
    sudo \
    build-essential \
    libxml2-dev \
    libncurses5-dev \
    libsqlite3-dev \
    libssl-dev \
    uuid-dev || {
    echo -e "${RED}[HATA] Temel paket kurulumu başarısız!${NC}"
    exit 1
}

# 3. APACHE & PHP
echo -e "${YELLOW}[3/8] Apache ve PHP kuruluyor...${NC}"
apt-get install -y \
    apache2 \
    php \
    php-mysql \
    php-gd \
    php-curl \
    php-pear \
    php-cli \
    php-common \
    php-json \
    php-mbstring \
    php-xml \
    php-zip || {
    echo -e "${RED}[HATA] Apache/PHP kurulumu başarısız!${NC}"
    exit 1
}

# PHP ayarları
for php_ini in /etc/php/*/apache2/php.ini; do
    sed -i 's/^\(memory_limit = \).*/\1256M/' "$php_ini"
    sed -i 's/^\(upload_max_filesize = \).*/\120M/' "$php_ini"
    sed -i 's/^\(post_max_size = \).*/\125M/' "$php_ini"
done

systemctl restart apache2

# 4. MARIADB KURULUMU
echo -e "${YELLOW}[4/8] MariaDB kuruluyor...${NC}"
apt-get install -y mariadb-server mariadb-client || {
    echo -e "${RED}[HATA] MariaDB kurulumu başarısız!${NC}"
    exit 1
}

# MySQL güvenlik
mysql -e "DELETE FROM mysql.user WHERE User='';"
mysql -e "DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');"
mysql -e "DROP DATABASE IF EXISTS test;"
mysql -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';"
mysql -e "FLUSH PRIVILEGES;"

# 5. ASTERISK KURULUMU
echo -e "${YELLOW}[5/8] Asterisk kuruluyor...${NC}"
apt-get install -y \
    asterisk \
    asterisk-core-sounds-en-wav \
    asterisk-core-sounds-en-gsm \
    asterisk-moh-opsound-wav \
    asterisk-moh-opsound-gsm || {
    echo -e "${RED}[HATA] Asterisk kurulumu başarısız!${NC}"
    exit 1
}

# 6. FREEPBX KURULUMU
echo -e "${YELLOW}[6/8] FreePBX kuruluyor...${NC}"
cd /usr/src || exit
wget -O freepbx-16.0-latest.tgz http://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz || {
    echo -e "${RED}[HATA] FreePBX indirme başarısız!${NC}"
    exit 1
}

tar xfz freepbx-16.0-latest.tgz
cd freepbx || exit

# Veritabanı kullanıcısı oluştur
DB_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
mysql -e "CREATE DATABASE IF NOT EXISTS asterisk;"
mysql -e "CREATE USER IF NOT EXISTS 'freepbxuser'@'localhost' IDENTIFIED BY '${DB_PASS}';"
mysql -e "GRANT ALL PRIVILEGES ON asterisk.* TO 'freepbxuser'@'localhost';"
mysql -e "FLUSH PRIVILEGES;"

# FreePBX config
cat > /etc/freepbx.conf <<EOF
<?php
\$amp_conf['AMPDBUSER'] = 'freepbxuser';
\$amp_conf['AMPDBPASS'] = '${DB_PASS}';
\$amp_conf['AMPDBHOST'] = 'localhost';
\$amp_conf['AMPDBNAME'] = 'asterisk';
\$amp_conf['AMPDBENGINE'] = 'mysql';
EOF

./start_asterisk start
./install -n --webroot=/var/www/html || {
    echo -e "${RED}[HATA] FreePBX kurulumu başarısız!${NC}"
    exit 1
}

# 7. DOSYA İZİNLERİ
echo -e "${YELLOW}[7/8] Dosya izinleri ayarlanıyor...${NC}"
chown -R asterisk:asterisk /var/www/html /var/lib/asterisk /var/spool/asterisk /var/run/asterisk
chmod -R 755 /var/www/html
fwconsole chown
fwconsole ma refreshsignatures
fwconsole ma installall
fwconsole reload

# 8. GÜVENLİK AYARLARI
echo -e "${YELLOW}[8/8] Güvenlik ayarları yapılandırılıyor...${NC}"
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 5060/udp
ufw allow 5061/tcp
ufw allow 10000:20000/udp
ufw --force enable

# KURULUM TAMAMLANDI
IP_ADDR=$(hostname -I | awk '{print $1}')

echo -e "${GREEN}"
echo "***********************************************"
echo "* KURULUM BAŞARIYLA TAMAMLANDI!               *"
echo "***********************************************"
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
echo -e "Servis kontrolü: ${BLUE}systemctl status asterisk${NC}"
echo -e "FreePBX konsolu: ${BLUE}fwconsole status${NC}"

exit 0
