#!/bin/bash
# pbxkur.sh - Temel sistem kurulumu (Apache, MySQL, PHP, Node.js 14, Türkiye zaman dilimi)

# Kök kontrolü
if [ "$(id -u)" -ne 0 ]; then
  echo "Bu script root olarak çalıştırılmalıdır!" >&2
  exit 1
fi

# Sistem güncellemeleri
echo "Sistem güncellemeleri yapılıyor..."
apt-get update && apt-get upgrade -y

# Türkiye zaman dilimini ayarla
echo "Türkiye zaman dilimi ayarlanıyor..."
timedatectl set-timezone Europe/Istanbul
apt-get install -y tzdata
ln -fs /usr/share/zoneinfo/Europe/Istanbul /etc/localtime
dpkg-reconfigure --frontend noninteractive tzdata

# Temel bağımlılıklar
echo "Temel bağımlılıklar kuruluyor..."
apt-get install -y wget curl git unzip build-essential sqlite3 libsqlite3-dev

# Apache kurulumu
echo "Apache kuruluyor..."
apt-get install -y apache2
systemctl enable apache2
systemctl start apache2

# MySQL kurulumu
echo "MySQL sunucusu kuruluyor..."
apt-get install -y mariadb-server mariadb-client
systemctl enable mariadb
systemctl start mariadb

# MySQL güvenlik ayarları
echo "MySQL güvenlik ayarları yapılandırılıyor..."
mysql_secure_installation <<EOF
n
y
pbxpassword123
pbxpassword123
y
y
y
y
EOF

# PHP kurulumu
echo "PHP ve gerekli modüller kuruluyor..."
apt-get install -y php php-mysql php-cli php-common php-gd php-pear php-curl php-xml php-mbstring php-json php-zip php-intl php-bcmath

# Node.js 14 kurulumu
echo "Node.js 14 kuruluyor..."
curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
apt-get install -y nodejs

# UFW ayarları
echo "Güvenlik duvarı ayarlanıyor..."
apt-get install -y ufw
ufw allow 22
ufw allow 80
ufw allow 443
ufw allow 5060/udp
ufw allow 5060/tcp
ufw allow 5061/udp
ufw allow 5061/tcp
ufw allow 10000:20000/udp
ufw enable

# Sistem temizliği
echo "Sistem temizliği yapılıyor..."
apt-get autoremove -y
apt-get clean

echo "Temel kurulum tamamlandı! Şimdi pbx.sh scriptini çalıştırabilirsiniz."
