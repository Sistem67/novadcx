#!/bin/bash

# Tüm FreePBX/Asterisk Paketlerini ve Konfigürasyonlarını Komple Kaldırma Scripti
# Uyarı: Bu script geri dönüşü olmayan bir temizlik yapacaktır!

# Renk Kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

# Log Dosyası
LOG_FILE="/var/log/freepbx_complete_removal_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo -e "${RED}"
echo "*******************************************************"
echo "* DİKKAT: Bu script tüm FreePBX/Asterisk bileşenlerini *"
echo "*         kalıcı olarak silecektir!                   *"
echo "*******************************************************"
echo -e "${NC}"

# Onay Sorgusu
read -p "Tüm FreePBX/Asterisk paketlerini silmek istediğinize emin misiniz? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}İşlem iptal edildi.${NC}"
    exit 1
fi

echo -e "${YELLOW}[1/5] Servisler durduruluyor...${NC}"
systemctl stop asterisk mariadb apache2 > /dev/null 2>&1
systemctl disable asterisk mariadb apache2 > /dev/null 2>&1

echo -e "${YELLOW}[2/5] Paketler kaldırılıyor...${NC}"
# Tüm ilgili paketleri kaldır
apt-get purge -y \
    asterisk* \
    freepbx* \
    mariadb* \
    apache2* \
    php* \
    nodejs* \
    dahdi* \
    libpri* \
    wanpipe* \
    fail2ban* \
    nginx* \
    > /dev/null 2>&1

# Kullanılmayan bağımlılıkları temizle
apt-get autoremove -y > /dev/null 2>&1
apt-get autoclean > /dev/null 2>&1

echo -e "${YELLOW}[3/5] Konfigürasyon dosyaları siliniyor...${NC}"
# Tüm konfigürasyon ve veri dosyalarını sil
rm -rf \
    /etc/asterisk \
    /var/lib/asterisk \
    /var/spool/asterisk \
    /var/log/asterisk \
    /usr/share/asterisk \
    /var/www/html \
    /etc/freepbx.conf \
    /etc/amportal.conf \
    /etc/dahdi \
    /etc/nginx \
    /etc/apache2 \
    /etc/php* \
    /usr/src/freepbx* \
    /usr/src/asterisk* \
    /tmp/freepbx* \
    /root/.npm \
    /var/lib/mysql \
    /var/log/mysql \
    /etc/mysql \
    > /dev/null 2>&1

echo -e "${YELLOW}[4/5] Kullanıcılar ve gruplar temizleniyor...${NC}"
# Kullanıcıları sil (eğer varsa)
userdel asterisk > /dev/null 2>&1
groupdel asterisk > /dev/null 2>&1
userdel mysql > /dev/null 2>&1
groupdel mysql > /dev/null 2>&1

echo -e "${YELLOW}[5/5] Sistem temizleniyor...${NC}"
# Geçici dosyaları temizle
find /var/log -name "asterisk*" -exec rm -rf {} \; > /dev/null 2>&1
find /var/log -name "freepbx*" -exec rm -rf {} \; > /dev/null 2>&1
find /tmp -name "freepbx*" -exec rm -rf {} \; > /dev/null 2>&1
find /tmp -name "asterisk*" -exec rm -rf {} \; > /dev/null 2>&1

# Paket veritabanını güncelle
apt-get update > /dev/null 2>&1

echo -e "${GREEN}"
echo "****************************************************"
echo "*  TÜM FreePBX/ASTERISK BİLEŞENLERİ BAŞARIYLA SİLİNDİ  *"
echo "****************************************************"
echo -e "${NC}"

echo -e "Yapılan işlemlerin detaylı kaydı: ${YELLOW}${LOG_FILE}${NC}"
echo -e "Sistem şu anda temel Ubuntu sunucusu durumuna getirildi."

# Son kontrol
echo -e "\nKalan ilgili dosyalar:"
find / -name "*asterisk*" 2>/dev/null
find / -name "*freepbx*" 2>/dev/null

exit 0
