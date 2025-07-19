#!/bin/bash

# FusionPBX Tam Otomatik Yeniden Kurulum Scripti
# Ubuntu 20.04/22.04 LTS için
# Versiyon: 3.0
# Önceki kurulumları tamamen siler ve yeniden kurar

# Renk tanımları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Kök kontrolü
if [ "$(id -u)" != "0" ]; then
   echo -e "${RED}HATA: Bu script root olarak çalıştırılmalıdır.${NC}" 1>&2
   exit 1
fi

# Ubuntu versiyon kontrolü
UBUNTU_VERSION=$(lsb_release -rs)
if [[ "$UBUNTU_VERSION" != "20.04" && "$UBUNTU_VERSION" != "22.04" ]]; then
    echo -e "${RED}HATA: Sadece Ubuntu 20.04 veya 22.04 desteklenmektedir.${NC}"
    exit 1
fi

# Değişkenler
IP_ADDRESS=$(hostname -I | awk '{print $1}')
DB_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
FS_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
ADMIN_PASS=$(openssl rand -base64 8 | tr -dc 'a-zA-Z0-9')

# Başlık
echo -e "${GREEN}"
echo "##########################################################"
echo "#     FUSIONPBX TAM OTOMATİK YENİDEN KURULUM SCRIPTİ     #"
echo "#               Ubuntu $UBUNTU_VERSION LTS               #"
echo "##########################################################"
echo -e "${NC}"
sleep 2

## 1. ÖNCEKİ KURULUMU TEMİZLEME ##
echo -e "${YELLOW}[1/12] Önceki kurulumlar temizleniyor...${NC}"

# Servisleri durdur
systemctl stop freeswitch 2>/dev/null
systemctl stop nginx 2>/dev/null
systemctl stop php*-fpm 2>/dev/null

# Paketleri kaldır
apt-get remove --purge -y freeswitch* fusionpbx* nginx* postgresql* 2>/dev/null

# Dosyaları sil
rm -rf /var/lib/freeswitch/
rm -rf /usr/local/freeswitch/
rm -rf /var/www/fusionpbx/
rm -rf /etc/freeswitch/
rm -rf /etc/fusionpbx/
rm -rf /var/log/freeswitch/

# PostgreSQL veritabanını sil
sudo -u postgres dropdb fusionpbx 2>/dev/null
sudo -u postgres dropuser fusionpbx 2>/dev/null

# Bağımlılıkları temizle
apt-get autoremove -y
apt-get clean

## 2. YENİ KURULUM İÇİN HAZIRLIK ##
echo -e "${YELLOW}[2/12] Sistem güncelleniyor...${NC}"
apt-get update -y
apt-get upgrade -y
apt-get install -y wget curl git unzip

## 3. GEREKLİ PAKETLER ##
echo -e "${YELLOW}[3/12] Temel paketler yükleniyor...${NC}"
apt-get install -y nginx postgresql postgresql-contrib

## 4. FUSIONPBX KURULUM DOSYASI ##
echo -e "${YELLOW}[4/12] FusionPBX kurulum dosyası indiriliyor...${NC}"
wget -O /tmp/fusionpbx-install.sh https://raw.githubusercontent.com/fusionpbx/fusionpbx-install.sh/master/ubuntu.sh
chmod +x /tmp/fusionpbx-install.sh

## 5. FUSIONPBX KURULUMU ##
echo -e "${YELLOW}[5/12] FusionPBX kurulumu başlatılıyor...${NC}"
export AUTO_ANSWER=true
export DB_PASSWORD=$DB_PASS
export FS_PASSWORD=$FS_PASS
/tmp/fusionpbx-install.sh

## 6. FREESWITCH KONTROLÜ ##
echo -e "${YELLOW}[6/12] FreeSWITCH kontrol ediliyor...${NC}"
if [ ! -f /usr/local/freeswitch/bin/freeswitch ]; then
    echo -e "${RED}HATA: FreeSWITCH kurulumu başarısız oldu. Manuel kurulum denenecek.${NC}"
    cd /usr/src
    git clone https://github.com/signalwire/freeswitch.git
    cd freeswitch
    ./configure -C
    make
    make install
    make cd-sounds-install
    make cd-moh-install
fi

## 7. SERVİSLERİ BAŞLAT ##
echo -e "${YELLOW}[7/12] Servisler yapılandırılıyor...${NC}"
systemctl daemon-reload
systemctl enable freeswitch
systemctl start freeswitch
systemctl restart postgresql
systemctl restart nginx

# PHP-FPM versiyon kontrolü
if [ -f "/etc/php/8.1/fpm/php.ini" ]; then
    systemctl restart php8.1-fpm
elif [ -f "/etc/php/7.4/fpm/php.ini" ]; then
    systemctl restart php7.4-fpm
else
    echo -e "${YELLOW}Uyarı: PHP-FPM servisi bulunamadı.${NC}"
fi

## 8. ADMIN ŞİFRESİ ##
echo -e "${YELLOW}[8/12] Yönetici hesabı ayarlanıyor...${NC}"
sudo -u postgres psql fusionpbx -c "UPDATE v_users SET password = md5('${ADMIN_PASS}') WHERE username = 'admin';"
sudo -u postgres psql fusionpbx -c "UPDATE v_users SET salt = '' WHERE username = 'admin';"

## 9. GÜVENLİK AYARLARI ##
echo -e "${YELLOW}[9/12] Güvenlik ayarları yapılandırılıyor...${NC}"
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 5060/tcp
ufw allow 5060/udp
ufw allow 5080/tcp
ufw allow 5080/udp
ufw allow 16384:32768/udp
echo "y" | ufw enable

## 10. NGINX YAPILANDIRMASI ##
echo -e "${YELLOW}[10/12] Web arayüzü ayarlanıyor...${NC}"
cat > /etc/nginx/sites-available/fusionpbx << EOL
server {
    listen 80;
    server_name $IP_ADDRESS;
    
    root /var/www/fusionpbx;
    index index.php index.html index.htm;
    
    location / {
        try_files \$uri \$uri/ /index.php?\$query_string;
    }
    
    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/var/run/php/php-fpm.sock;
    }
}
EOL

ln -s /etc/nginx/sites-available/fusionpbx /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

## 11. SİSTEM AYARLARI ##
echo -e "${YELLOW}[11/12] Sistem ayarları güncelleniyor...${NC}"
sudo -u postgres psql fusionpbx -c "UPDATE v_default_settings SET default_setting_value = '$IP_ADDRESS' WHERE default_setting_subcategory = 'domain';"
sudo -u postgres psql fusionpbx -c "UPDATE v_domains SET domain_name = '$IP_ADDRESS';"

## 12. KURULUM TAMAMLANDI ##
echo -e "${GREEN}"
echo "##########################################################"
echo "#           KURULUM BAŞARIYLA TAMAMLANDI!               #"
echo "##########################################################"
echo ""
echo -e "${CYAN}ERİŞİM BİLGİLERİ:${NC}"
echo -e "Yönetim Paneli: ${GREEN}http://$IP_ADDRESS${NC}"
echo -e "Kullanıcı Adı: ${GREEN}admin${NC}"
echo -e "Şifre: ${GREEN}$ADMIN_PASS${NC}"
echo ""
echo -e "${CYAN}VERİTABANI BİLGİLERİ:${NC}"
echo -e "Veritabanı Şifresi: ${GREEN}$DB_PASS${NC}"
echo -e "FreeSWITCH Şifresi: ${GREEN}$FS_PASS${NC}"
echo ""
echo -e "${YELLOW}ÖNEMLİ NOTLAR:${NC}"
echo "1. İlk girişte admin şifresini hemen değiştirin!"
echo "2. Kurulum dosyalarını kaldırın:"
echo "   rm -rf /var/www/fusionpbx/core/install/"
echo "3. Üretim ortamında mutlaka SSL kullanın"
echo "4. Sistem yedeğini almayı unutmayın"
echo -e "${GREEN}"
echo "##########################################################"
echo -e "${NC}"
