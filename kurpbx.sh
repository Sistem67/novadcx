#!/bin/bash
# Ubuntu 22.04 için Hatasız FreePBX Kurulum Scripti
# Tüm bağımlılıklar, kontroller ve hata çözümleri dahil

# === [KONFİGÜRASYON] ===
ASTERISK_VERSION="20.2.0"
FREEPBX_VERSION="16.0.29.5"
MYSQL_ROOT_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
ASTERISK_DB_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
FREEPBX_ADMIN_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9')
INSTALL_DIR="/opt/freepbx_install"
LOG_FILE="/var/log/freepbx_install_$(date +%Y%m%d_%H%M%S).log"

# === [FONKSİYONLAR] ===
function die {
  echo -e "\033[1;31mHATA: $1\033[0m"
  echo -e "\033[1;33mDetaylar için log dosyasına bakın: $LOG_FILE\033[0m"
  exit 1
}

function check_internet {
  if ! ping -c 1 google.com &> /dev/null; then
    die "Internet bağlantısı yok!"
  fi
}

function cleanup {
  echo -e "\n\033[1;33mTemizlik yapılıyor...\033[0m"
  rm -rf ${INSTALL_DIR}/asterisk-*
  rm -rf ${INSTALL_DIR}/freepbx-*
  apt-get autoremove -y
  apt-get clean
}

# === [KURULUM BAŞLANGICI] ===
clear
echo -e "\033[1;36mFreePBX Hatasız Kurulum Scripti\033[0m"
echo -e "\033[1;36mUbuntu 22.04 LTS için Tam Otomatik\033[0m"
echo "----------------------------------------"

# Root kontrolü
if [ "$(id -u)" != "0" ]; then
  die "Bu script root olarak çalıştırılmalıdır!"
fi

# Log dosyası
exec > >(tee -a "$LOG_FILE") 2>&1
echo -e "\n===== [Kurulum Başlangıç: $(date)] ====="

# === [1. SİSTEM KONTROLLERİ] ===
echo -e "\n[1/10] Sistem Kontrolleri Yapılıyor..."
check_internet

# Ubuntu versiyon kontrolü
if ! grep -q "Ubuntu 22.04" /etc/os-release; then
  die "Sadece Ubuntu 22.04 desteklenmektedir!"
fi

# Disk alanı kontrolü (minimum 5GB)
if [ $(df --output=avail / | tail -n1) -lt 5000000 ]; then
  die "Yetersiz disk alanı! Minimum 5GB boş alan gereklidir."
fi

# === [2. DEPO AYARLARI] ===
echo -e "\n[2/10] Depo Ayarları Yapılıyor..."
{
  # Universe deposunu garantili etkinleştirme
  sed -i '/^# deb.*universe/s/^# //' /etc/apt/sources.list || add-apt-repository universe -y
  
  # NodeSource deposu
  curl -fsSL https://deb.nodesource.com/setup_16.x | bash - || die "NodeSource deposu eklenemedi"
  
  apt-get update -y || die "Paket listesi güncellenemedi"
  apt-get upgrade -y || die "Sistem güncellemeleri yapılamadı"
  apt-get install -f -y || die "Bozuk paketler onarılamadı"
} || die "Depo ayarlarında hata oluştu"

# === [3. TEMEL BAĞIMLILIKLER] ===
echo -e "\n[3/10] Temel Bağımlılıklar Yükleniyor..."
{
  apt-get install -y \
    wget curl git build-essential libssl-dev libncurses5-dev \
    subversion libjansson-dev sqlite3 autoconf automake libtool \
    pkg-config unixodbc unixodbc-dev uuid uuid-dev libasound2-dev \
    libogg-dev libvorbis-dev libcurl4-openssl-dev libical-dev \
    libneon27-dev libsrtp2-dev libspandsp-dev libedit-dev libldap2-dev \
    libmemcached-dev libspeex-dev libspeexdsp-dev libsrtp2-dev \
    libxml2-dev libsqlite3-dev libpq-dev libiksemel-dev \
    libnewt-dev libusb-dev libpopt-dev liblua5.2-dev libopus-dev \
    libresample1-dev libvpb-dev sofia-sip-bin \
    apache2 mariadb-server php php-mysql php-gd php-curl php-mbstring \
    php-zip php-xml php-json php-cli libapache2-mod-php php-intl \
    php-bcmath php-soap php-ldap php-snmp nodejs npm fail2ban sngrep \
    || die "Bağımlılık yükleme hatası"

  # Node.js alternatif yükleme
  if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_16.x | bash -
    apt-get install -y nodejs || die "NodeJS yükleme hatası"
  fi
  
  npm config set cache /tmp/npm_cache --global
  npm install -g npm@latest pm2
} || die "Bağımlılık kurulumunda hata oluştu"

# === [4. MYSQL YAPILANDIRMASI] ===
echo -e "\n[4/10] MySQL Yapılandırması Yapılıyor..."
{
  systemctl start mariadb
  systemctl enable mariadb

  # MySQL güvenlik ayarları
  mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '$MYSQL_ROOT_PASS';"
  mysql -uroot -p$MYSQL_ROOT_PASS -e "DELETE FROM mysql.user WHERE User='';"
  mysql -uroot -p$MYSQL_ROOT_PASS -e "DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');"
  mysql -uroot -p$MYSQL_ROOT_PASS -e "DROP DATABASE IF EXISTS test;"
  mysql -uroot -p$MYSQL_ROOT_PASS -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';"
  
  # Asterisk veritabanı
  mysql -uroot -p$MYSQL_ROOT_PASS -e "CREATE DATABASE asterisk CHARACTER SET utf8 COLLATE utf8_unicode_ci;"
  mysql -uroot -p$MYSQL_ROOT_PASS -e "CREATE USER 'asteriskuser'@'localhost' IDENTIFIED BY '$ASTERISK_DB_PASS';"
  mysql -uroot -p$MYSQL_ROOT_PASS -e "GRANT ALL PRIVILEGES ON asterisk.* TO 'asteriskuser'@'localhost';"
  mysql -uroot -p$MYSQL_ROOT_PASS -e "FLUSH PRIVILEGES;"
  
  # MySQL optimizasyonları
  sed -i '/^\[mysqld\]/a \
innodb_buffer_pool_size = 256M\
innodb_log_file_size = 128M\
innodb_lock_wait_timeout = 90\
innodb_flush_log_at_trx_commit = 2' /etc/mysql/mariadb.conf.d/50-server.cnf
  
  systemctl restart mariadb
} || die "MySQL yapılandırmasında hata oluştu"

# === [5. ASTERISK KURULUMU] ===
echo -e "\n[5/10] Asterisk Derleme ve Kurulumu..."
{
  mkdir -p $INSTALL_DIR
  cd $INSTALL_DIR
  
  # Asterisk indirme
  wget -q http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz \
    || wget -q http://downloads.asterisk.org/pub/telephony/asterisk/old-releases/asterisk-${ASTERISK_VERSION}.tar.gz \
    || die "Asterisk indirme hatası"
  
  tar xf asterisk-${ASTERISK_VERSION}.tar.gz || die "Asterisk arşivi açılamadı"
  cd asterisk-${ASTERISK_VERSION} || die "Asterisk dizinine geçilemedi"
  
  # Bağımlılıklar
  contrib/scripts/install_prereq install || die "Asterisk bağımlılıkları yüklenemedi"
  
  # Yapılandırma
  ./configure --with-jansson-bundled --with-pjproject-bundled || die "Asterisk configure hatası"
  
  # Menuselect ayarları
  make menuselect.makeopts
  ./menuselect/menuselect \
    --disable-category MENUSELECT_CDR \
    --disable-category MENUSELECT_CEL \
    --disable-category MENUSELECT_CHANNELS \
    --enable CORE-SOUNDS-EN-WAV \
    --enable CORE-SOUNDS-EN-ULAW \
    --enable codec_opus \
    --enable codec_silk \
    --enable format_mp3 \
    --enable res_config_mysql \
    --enable app_mysql \
    --enable cdr_mysql \
    menuselect.makeopts || die "Menuselect yapılandırma hatası"
  
  # Derleme
  make -j$(nproc) || die "Asterisk derleme hatası"
  make install || die "Asterisk kurulum hatası"
  make config || die "Asterisk config hatası"
  ldconfig
  
  # Kullanıcı ve izinler
  if ! id asterisk &>/dev/null; then
    useradd -r -d /var/lib/asterisk -s /bin/bash -c "Asterisk PBX" asterisk
  fi
  usermod -aG audio,dialout asterisk
  mkdir -p /var/{lib,log,run,spool}/asterisk
  chown -R asterisk:asterisk /var/{lib,log,run,spool}/asterisk /etc/asterisk
} || die "Asterisk kurulumunda hata oluştu"

# === [6. FREEPBX KURULUMU] ===
echo -e "\n[6/10] FreePBX Kurulumu Yapılıyor..."
{
  cd $INSTALL_DIR
  
  # FreePBX indirme
  wget -q https://mirror.freepbx.org/modules/packages/freepbx/freepbx-${FREEPBX_VERSION}.tgz \
    || die "FreePBX indirme hatası"
  
  tar xf freepbx-${FREEPBX_VERSION}.tgz || die "FreePBX arşivi açılamadı"
  cd freepbx || die "FreePBX dizinine geçilemedi"
  
  # Asterisk servisi
  ./start_asterisk start || die "Asterisk başlatılamadı"
  
  # FreePBX kurulumu
  ./install -n \
    --dbengine=mysql \
    --dbhost=localhost \
    --dbname=asterisk \
    --dbuser=asteriskuser \
    --dbpass=$ASTERISK_DB_PASS \
    --user=asterisk \
    --group=asterisk \
    --webroot=/var/www/html \
    || die "FreePBX kurulum hatası"
  
  # Admin şifresi
  /usr/sbin/fwconsole chpass --username=admin --password=$FREEPBX_ADMIN_PASS \
    || die "Admin şifresi ayarlanamadı"
} || die "FreePBX kurulumunda hata oluştu"

# === [7. APACHE YAPILANDIRMASI] ===
echo -e "\n[7/10] Apache Yapılandırılıyor..."
{
  # Apache modülleri
  a2enmod rewrite headers expires deflate || die "Apache modülleri etkinleştirilemedi"
  
  # PHP ayarları
  for config in /etc/php/*/apache2/php.ini; do
    sed -i 's/^\(memory_limit =\).*/\1 256M/' $config
    sed -i 's/^\(upload_max_filesize =\).*/\1 20M/' $config
    sed -i 's/^\(post_max_size =\).*/\1 20M/' $config
    sed -i 's/^\(max_execution_time =\).*/\1 300/' $config
    sed -i 's/^\(max_input_time =\).*/\1 300/' $config
    sed -i 's/^\(;date.timezone =\).*/\1 Europe\/Istanbul/' $config
  done
  
  # FreePBX için VirtualHost
  cat > /etc/apache2/sites-available/freepbx.conf <<EOF
<VirtualHost *:80>
    ServerAdmin webmaster@localhost
    DocumentRoot /var/www/html
    <Directory /var/www/html>
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
    ErrorLog \${APACHE_LOG_DIR}/freepbx_error.log
    CustomLog \${APACHE_LOG_DIR}/freepbx_access.log combined
</VirtualHost>
EOF

  # Varsayılan siteyi devre dışı bırak
  a2dissite 000-default.conf > /dev/null
  a2ensite freepbx.conf > /dev/null
  
  # Apache servisi
  systemctl restart apache2 || die "Apache yeniden başlatılamadı"
} || die "Apache yapılandırmasında hata oluştu"

# === [8. GÜVENLİK AYARLARI] ===
echo -e "\n[8/10] Güvenlik Ayarları Yapılandırılıyor..."
{
  # Firewall ayarları
  ufw allow 22/tcp comment 'SSH'
  ufw allow 80/tcp comment 'HTTP'
  ufw allow 443/tcp comment 'HTTPS'
  ufw allow 5060/tcp comment 'SIP TCP'
  ufw allow 5060/udp comment 'SIP UDP'
  ufw allow 5061/tcp comment 'SIP TLS'
  ufw allow 10000:20000/udp comment 'RTP Ports'
  echo "y" | ufw enable || die "Firewall etkinleştirilemedi"
  
  # Fail2Ban
  cat > /etc/fail2ban/jail.d/freepbx.conf <<EOF
[freepbx]
enabled = true
port = 80,443,5060,5061
filter = freepbx
logpath = /var/log/asterisk/full
maxretry = 5
findtime = 600
bantime = 3600
EOF

  systemctl enable fail2ban
  systemctl restart fail2ban
  
  # Dosya izinleri
  chown -R asterisk:asterisk /var/www/html
  find /var/www/html -type d -exec chmod 755 {} \;
  find /var/www/html -type f -exec chmod 644 {} \;
  chmod -R 775 /var/www/html/admin/modules
  chmod 755 /var/lib/asterisk/bin/*
} || die "Güvenlik yapılandırmasında hata oluştu"

# === [9. FREEPBX MODÜLLERİ] ===
echo -e "\n[9/10] FreePBX Modülleri Yükleniyor..."
{
  # Temel modüller
  /usr/sbin/fwconsole ma install core framework sipsettings pm2 asteriskinfo \
    || die "Temel modüller yüklenemedi"
  
  # Modül güncellemeleri
  /usr/sbin/fwconsole ma upgradeall || echo "Modül güncellemeleri atlandı"
  /usr/sbin/fwconsole ma refreshsignatures || echo "İmza yenileme atlandı"
  
  # PM2 yapılandırması
  sudo -u asterisk pm2 start /var/www/html/admin/modules/pm2/node_modules/pm2
  sudo -u asterisk pm2 save
  sudo -u asterisk pm2 startup
} || die "FreePBX modül yüklemesinde hata oluştu"

# === [10. SERVİSLERİN BAŞLATILMASI] ===
echo -e "\n[10/10] Servisler Başlatılıyor..."
{
  # Sistem servisleri
  systemctl enable asterisk
  systemctl start asterisk || die "Asterisk başlatılamadı"
  systemctl restart apache2 || die "Apache yeniden başlatılamadı"
  
  # FreePBX cron job
  echo "*/5 * * * * /usr/sbin/fwconsole cron --quiet" | crontab -u asterisk -
  
  # FreePBX servisi
  /usr/sbin/fwconsole reload || die "FreePBX reload hatası"
  /usr/sbin/fwconsole restart || die "FreePBX restart hatası"
} || die "Servis başlatmada hata oluştu"

# === [KURULUM TAMAMLANDI] ===
cleanup
IP_ADDR=$(hostname -I | awk '{print $1}')

echo -e "\n\n\033[1;32m===== FREEPBX KURULUMU BAŞARIYLA TAMAMLANDI =====\033[0m"
echo -e "\033[1;36mErişim Bilgileri:\033[0m"
echo -e "  - Web Arayüz: \033[1;35mhttp://$IP_ADDR\033[0m"
echo -e "  - Kullanıcı Adı: \033[1;35madmin\033[0m"
echo -e "  - Şifre: \033[1;35m$FREEPBX_ADMIN_PASS\033[0m"
echo -e "\n\033[1;36mVeritabanı Bilgileri:\033[0m"
echo -e "  - MySQL Root Şifre: \033[1;35m$MYSQL_ROOT_PASS\033[0m"
echo -e "  - Asterisk DB Kullanıcı: \033[1;35masteriskuser\033[0m"
echo -e "  - Asterisk DB Şifre: \033[1;35m$ASTERISK_DB_PASS\033[0m"
echo -e "\n\033[1;33mLog Dosyası: \033[1;35m$LOG_FILE\033[0m"
echo -e "\033[1;31mNOT: Bu bilgileri güvenli bir yere kaydedin!\033[0m"
echo -e "\033[1;32m=============================================\033[0m"

exit 0
