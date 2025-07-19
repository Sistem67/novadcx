#!/bin/bash

# FusionPBX Tam Otomatik Kurulum Scripti (Alternatif Kaynaklı)
# Ubuntu 20.04/22.04 LTS için
# Versiyon: 2.1
# Depo sorunları giderilmiş hali

# Renk tanımları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Kök kontrolü
if [ "$(id -u)" != "0" ]; then
   echo -e "${RED}Hata: Bu script root olarak çalıştırılmalıdır.${NC}" 1>&2
   exit 1
fi

# Ubuntu versiyon kontrolü
UBUNTU_VERSION=$(lsb_release -rs)
if [[ "$UBUNTU_VERSION" != "20.04" && "$UBUNTU_VERSION" != "22.04" ]]; then
    echo -e "${RED}Hata: Sadece Ubuntu 20.04 veya 22.04 desteklenmektedir.${NC}"
    exit 1
fi

# Değişkenler
IP_ADDRESS=$(hostname -I | awk '{print $1}')
DB_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
FS_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
ADMIN_PASS=$(openssl rand -base64 8 | tr -dc 'a-zA-Z0-9')
DOMAIN=$IP_ADDRESS

# Başlık
echo -e "${GREEN}"
echo "##########################################################"
echo "#      FUSIONPBX TAM OTOMATİK KURULUM (ALTERNATİF)       #"
echo "#               Ubuntu $UBUNTU_VERSION LTS               #"
echo "##########################################################"
echo -e "${NC}"
sleep 2

# 1. Sistem güncellemeleri
echo -e "${YELLOW}[1/12] Sistem güncellemeleri yapılıyor...${NC}"
apt-get update -y
apt-get upgrade -y
apt-get install -y wget curl git unzip

# 2. Temel bağımlılıklar
echo -e "${YELLOW}[2/12] Temel bağımlılıklar yükleniyor...${NC}"
apt-get install -y nginx
systemctl start nginx
systemctl enable nginx

# 3. PostgreSQL kurulumu
echo -e "${YELLOW}[3/12] PostgreSQL kurulumu...${NC}"
apt-get install -y postgresql postgresql-contrib
systemctl restart postgresql

# 4. Alternatif FusionPBX kurulum dosyası
echo -e "${YELLOW}[4/12] Alternatif kurulum dosyası indiriliyor...${NC}"
wget -O /tmp/fusionpbx-install.sh https://raw.githubusercontent.com/signalwire/fusionpbx-install.sh/master/ubuntu.sh
chmod +x /tmp/fusionpbx-install.sh

# 5. FusionPBX kurulumu
echo -e "${YELLOW}[5/12] FusionPBX kurulumu başlatılıyor...${NC}"
export AUTO_ANSWER=true
export DB_PASSWORD=$DB_PASS
export FS_PASSWORD=$FS_PASS
/tmp/fusionpbx-install.sh

# 6. FreeSWITCH kaynaklarından kurulum
echo -e "${YELLOW}[6/12] FreeSWITCH kurulumu kontrol ediliyor...${NC}"
if [ ! -d /usr/local/freeswitch ]; then
    echo -e "${CYAN}FreeSWITCH kurulumu tamamlanıyor...${NC}"
    cd /usr/src
    git clone https://github.com/signalwire/freeswitch.git
    cd freeswitch
    ./configure -C
    make
    make install
    make cd-sounds-install
    make cd-moh-install
fi

# 7. Servislerin başlatılması
echo -e "${YELLOW}[7/12] Servisler yapılandırılıyor...${NC}"
systemctl daemon-reload
systemctl enable freeswitch
systemctl start freeswitch
systemctl restart postgresql
systemctl restart nginx
systemctl restart php7.4-fpm || systemctl restart php8.1-fpm

# 8. Admin şifresinin ayarlanması
echo -e "${YELLOW}[8/12] Yönetici hesabı ayarlanıyor...${NC}"
sudo -u postgres psql fusionpbx -c "UPDATE v_users SET password = md5('${ADMIN_PASS}') WHERE username = 'admin';"
sudo -u postgres psql fusionpbx -c "UPDATE v_users SET salt = '' WHERE username = 'admin';"

# 9. Güvenlik ayarları
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

# 10. Nginx yapılandırması
echo -e "${YELLOW}[10/12] Web arayüzü ayarlanıyor...${NC}"
cat > /etc/nginx/sites-available/fusionpbx << EOL
server {
    listen 80;
    server_name $DOMAIN;
    
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

# 11. Sistem ayarlarının güncellenmesi
echo -e "${YELLOW}[11/12] Sistem ayarları güncelleniyor...${NC}"
sudo -u postgres psql fusionpbx -c "UPDATE v_default_settings SET default_setting_value = '$IP_ADDRESS' WHERE default_setting_subcategory = 'domain';"
sudo -u postgres psql fusionpbx -c "UPDATE v_domains SET domain_name = '$IP_ADDRESS';"

# 12. Kurulum tamamlandı
echo -e "${GREEN}"
echo "##########################################################"
echo "#           KURULUM BAŞARIYLA TAMAMLANDI!               #"
echo "##########################################################"
echo ""
echo -e "${CYAN}Erişim Bilgileri:${NC}"
echo -e "Yönetim Paneli: ${GREEN}http://$IP_ADDRESS${NC}"
echo -e "Kullanıcı Adı: ${GREEN}admin${NC}"
echo -e "Şifre: ${GREEN}$ADMIN_PASS${NC}"
echo ""
echo -e "${CYAN}Veritabanı Bilgileri:${NC}"
echo -e "Veritabanı Şifresi: ${GREEN}$DB_PASS${NC}"
echo -e "FreeSWITCH Şifresi: ${GREEN}$FS_PASS${NC}"
echo ""
echo -e "${YELLOW}Önemli Notlar:${NC}"
echo "1. İlk girişte admin şifresini hemen değiştirin!"
echo "2. /var/www/fusionpbx/core/install/ dizinini silin:"
echo "   rm -rf /var/www/fusionpbx/core/install/"
echo "3. Üretim ortamında mutlaka SSL kullanın"
echo -e "${GREEN}"
echo "##########################################################"
echo -e "${NC}"
