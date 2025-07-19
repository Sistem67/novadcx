#!/bin/bash

# FusionPBX & FreeSWITCH Tamamen Kaldırma Scripti
# Tüm bileşenleri ve konfigürasyonları siler

# Renk tanımları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Kök kontrolü
if [ "$(id -u)" != "0" ]; then
   echo -e "${RED}HATA: Bu script root olarak çalıştırılmalıdır.${NC}" 1>&2
   exit 1
fi

echo -e "${RED}"
echo "##########################################################"
echo "#     FUSIONPBX VE FREESWITCH TAMAMEN KALDIRILACAK!      #"
echo "#  Bu işlem geri alınamaz! Tüm veriler silinecektir!     #"
echo "##########################################################"
echo -e "${NC}"

# Onay isteme
read -p "Devam etmek istiyor musunuz? (E/H): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Ee]$ ]]; then
    echo -e "${YELLOW}Kaldırma işlemi iptal edildi.${NC}"
    exit 0
fi

echo -e "${YELLOW}[1/7] Servisler durduruluyor...${NC}"
systemctl stop freeswitch 2>/dev/null
systemctl stop nginx 2>/dev/null
systemctl stop php*-fpm 2>/dev/null
systemctl stop postgresql 2>/dev/null

echo -e "${YELLOW}[2/7] Paketler kaldırılıyor...${NC}"
apt-get remove --purge -y \
    freeswitch* \
    fusionpbx* \
    nginx* \
    postgresql* \
    php*-fpm \
    php*-common \
    php*-cli \
    php*-xml \
    php*-curl \
    php*-pgsql \
    certbot* \
    python3-certbot-nginx \
    fail2ban* \
    git \
    unzip \
    wget \
    curl 2>/dev/null

echo -e "${YELLOW}[3/7] Konfigürasyon dosyaları siliniyor...${NC}"
rm -rf /var/lib/freeswitch/
rm -rf /usr/local/freeswitch/
rm -rf /var/www/fusionpbx/
rm -rf /etc/freeswitch/
rm -rf /etc/fusionpbx/
rm -rf /var/log/freeswitch/
rm -rf /etc/nginx/
rm -rf /var/log/nginx/
rm -rf /etc/postgresql/
rm -rf /var/lib/postgresql/
rm -rf /etc/letsencrypt/
rm -rf /etc/fail2ban/
rm -rf /usr/share/freeswitch/
rm -rf /usr/src/freeswitch/
rm -rf /tmp/fusionpbx-install.sh

echo -e "${YELLOW}[4/7] PostgreSQL veritabanı temizleniyor...${NC}"
sudo -u postgres dropdb fusionpbx 2>/dev/null
sudo -u postgres dropuser fusionpbx 2>/dev/null
sudo -u postgres dropdb freeswitch 2>/dev/null
sudo -u postgres dropuser freeswitch 2>/dev/null

echo -e "${YELLOW}[5/7] Sistem kullanıcıları kaldırılıyor...${NC}"
userdel -r freeswitch 2>/dev/null
userdel -r fusionpbx 2>/dev/null
userdel -r www-data 2>/dev/null
groupdel freeswitch 2>/dev/null
groupdel fusionpbx 2>/dev/null

echo -e "${YELLOW}[6/7] Artık bağımlılıklar temizleniyor...${NC}"
apt-get autoremove -y
apt-get clean
apt-get autoclean

echo -e "${YELLOW}[7/7] Servisler yeniden başlatılıyor...${NC}"
systemctl daemon-reload

echo -e "${GREEN}"
echo "##########################################################"
echo "#   FUSIONPBX VE FREESWITCH TAMAMEN KALDIRILDI!         #"
echo "##########################################################"
echo -e "${NC}"

echo -e "${YELLOW}Önerilen son adımlar:${NC}"
echo "1. Sisteminizi yeniden başlatmanız önerilir:"
echo "   sudo reboot"
echo "2. Eğer tam bir temizlik istiyorsanız, yedeklerinizi aldıktan sonra:"
echo "   sudo apt-get remove --purge nginx* php* postgresql*"
echo "   sudo apt-get autoremove --purge"
echo "   sudo rm -rf /etc/nginx /etc/php /etc/postgresql /var/lib/postgresql"
