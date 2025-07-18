#!/bin/bash

# Tam Otomatik FreePBX Santral Kurulum Scripti
# Versiyon: 1.0
# Son Güncelleme: 2023-10-15
# Sistem Gereksinimleri: Ubuntu 20.04/22.04 LTS (64-bit), Minimum 2GB RAM, 20GB Disk

# Renk Tanımları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Root Kontrolü
if [ "$(id -u)" != "0" ]; then
   echo -e "${RED}Hata: Bu script root olarak çalıştırılmalıdır.${NC}" 1>&2
   exit 1
fi

# Başlık
clear
echo -e "${GREEN}"
echo "****************************************************"
echo "*      Ubuntu FreePBX Tam Otomatik Kurulum Scripti  *"
echo "*          Asterisk 16 + FreePBX 16 + GUI           *"
echo "****************************************************"
echo -e "${NC}"

# Log Dosyası
LOG_FILE="/var/log/freepbx_install.log"
echo "Kurulum Logu: $LOG_FILE"
echo "Kurulum başlangıç zamanı: $(date)" > $LOG_FILE

# 1. Sistem Güncellemeleri
echo -e "${YELLOW}[1/8] Sistem güncellemeleri yapılıyor...${NC}"
apt-get update >> $LOG_FILE 2>&1
apt-get upgrade -y >> $LOG_FILE 2>&1
echo -e "${GREEN}==> Sistem güncellemeleri tamamlandı.${NC}"

# 2. Temel Paketlerin Kurulumu
echo -e "${YELLOW}[2/8] Temel paketler kuruluyor...${NC}"
apt-get install -y wget curl git gnupg2 sudo net-tools ufw >> $LOG_FILE 2>&1
echo -e "${GREEN}==> Temel paketler kuruldu.${NC}"

# 3. Apache, PHP ve MariaDB Kurulumu
echo -e "${YELLOW}[3/8] Web sunucusu ve veritabanı kuruluyor...${NC}"
apt-get install -y apache2 mariadb-server mariadb-client >> $LOG_FILE 2>&1
apt-get install -y php php-mysql php-gd php-curl php-pear php-cli php-common php-json php-readline php-mbstring php-xml >> $LOG_FILE 2>&1

# MariaDB Güvenlik Ayarları
echo -e "${BLUE}--> MariaDB güvenlik ayarları yapılandırılıyor...${NC}"
mysql -e "DELETE FROM mysql.user WHERE User='';"
mysql -e "DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');"
mysql -e "DROP DATABASE IF EXISTS test;"
mysql -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';"
mysql -e "FLUSH PRIVILEGES;"
echo -e "${GREEN}==> Web sunucusu ve veritabanı kurulumu tamamlandı.${NC}"

# 4. Asterisk ve Bağımlılıklar
echo -e "${YELLOW}[4/8] Asterisk ve bağımlılıklar kuruluyor...${NC}"
apt-get install -y build-essential libxml2-dev libncurses5-dev libsqlite3-dev libssl-dev uuid-dev >> $LOG_FILE 2>&1
apt-get install -y asterisk asterisk-core-sounds-en-wav asterisk-core-sounds-en-gsm asterisk-moh-opsound-wav asterisk-moh-opsound-gsm >> $LOG_FILE 2>&1
echo -e "${GREEN}==> Asterisk kurulumu tamamlandı.${NC}"

# 5. FreePBX Kurulumu
echo -e "${YELLOW}[5/8] FreePBX kuruluyor...${NC}"
cd /usr/src
wget http://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz >> $LOG_FILE 2>&1
tar xfz freepbx-16.0-latest.tgz >> $LOG_FILE 2>&1
cd freepbx
./start_asterisk start >> $LOG_FILE 2>&1
./install -n >> $LOG_FILE 2>&1
echo -e "${GREEN}==> FreePBX kurulumu tamamlandı.${NC}"

# 6. Servislerin Ayarlanması
echo -e "${YELLOW}[6/8] Servisler yapılandırılıyor...${NC}"
systemctl enable asterisk >> $LOG_FILE 2>&1
systemctl enable apache2 >> $LOG_FILE 2>&1
systemctl enable mariadb >> $LOG_FILE 2>&1
fwconsole restart >> $LOG_FILE 2>&1
echo -e "${GREEN}==> Servisler yapılandırıldı.${NC}"

# 7. Güvenlik Duvarı Ayarları
echo -e "${YELLOW}[7/8] Güvenlik duvarı ayarlanıyor...${NC}"
ufw allow 80/tcp >> $LOG_FILE 2>&1
ufw allow 443/tcp >> $LOG_FILE 2>&1
ufw allow 5060/udp >> $LOG_FILE 2>&1
ufw allow 10000:20000/udp >> $LOG_FILE 2>&1
ufw allow 22/tcp >> $LOG_FILE 2>&1
ufw --force enable >> $LOG_FILE 2>&1
echo -e "${GREEN}==> Güvenlik duvarı ayarları tamamlandı.${NC}"

# 8. Kurulum Sonu Bilgileri
IP_ADDR=$(hostname -I | awk '{print $1}')
echo -e "${YELLOW}[8/8] Kurulum tamamlandı!${NC}"
echo -e "${GREEN}"
echo "****************************************************"
echo "*                   KURULUM TAMAMLANDI             *"
echo "****************************************************"
echo -e "${NC}"
echo -e "FreePBX Yönetim Paneli: ${BLUE}http://$IP_ADDR/admin${NC}"
echo -e "Varsayılan Kullanıcı: ${YELLOW}admin${NC}"
echo -e "Varsayılan Şifre: ${YELLOW}admin${NC}"
echo ""
echo -e "${RED}ÖNEMLİ: Güvenlik için ilk girişte şifrenizi değiştirin!${NC}"
echo ""
echo -e "Kurulum logları: ${BLUE}$LOG_FILE${NC}"
echo -e "Sistem durumunu kontrol etmek için: ${BLUE}fwconsole status${NC}"
echo -e "Servisleri yeniden başlatmak için: ${BLUE}fwconsole restart${NC}"

# Script Sonu
exit 0
