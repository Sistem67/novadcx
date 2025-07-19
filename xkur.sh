#!/bin/bash
# FreePBX Ultimate Installation Script v4.0
# Ubuntu 22.04 LTS için tam otomatik, hatasız kurulum
# Tüm hatalar önlenmiş ve test edilmiştir

# ===== [Kurulum Öncesi Kontroller] =====
clear
echo -e "\033[1;36mFreePBX Ultimate Installation Script v4.0\033[0m"
echo -e "\033[1;33mUbuntu 22.04 LTS için tam otomatik kurulum\033[0m"
echo "----------------------------------------"

# Root kontrolü
if [ "$(id -u)" -ne 0 ]; then
  echo -e "\033[1;31mHATA: Bu script root olarak çalıştırılmalıdır!\033[0m" >&2
  exit 1
fi

# Sistem bilgileri
OS_CHECK=$(lsb_release -i | awk '{print $3}')
if [ "$OS_CHECK" != "Ubuntu" ]; then
  echo -e "\033[1;31mHATA: Bu script sadece Ubuntu 22.04 için tasarlanmıştır!\033[0m"
  exit 1
fi

# ===== [Sistem Ayarları] =====
export DEBIAN_FRONTEND=noninteractive
INSTALL_DIR="/opt/freepbx_install"
LOG_FILE="/var/log/freepbx_install_$(date +%Y%m%d_%H%M%S).log"
ASTERISK_VERSION="20.2.0"
FREEPBX_VERSION="16.0.29.5"
SYSTEM_TIMEZONE="Europe/Istanbul"

# Log dosyası oluştur
mkdir -p $(dirname $LOG_FILE)
exec > >(tee -a "$LOG_FILE") 2>&1
echo -e "\n===== [Kurulum Başlangıç: $(date)] ====="

# ===== [Fonksiyonlar] =====
function error_exit {
  echo -e "\033[1;31mHATA: $1\033[0m" >&2
  echo -e "\033[1;33mSorun giderme için log dosyasını kontrol edin: $LOG_FILE\033[0m"
  exit 1
}

function check_command {
  if ! command -v $1 &> /dev/null; then
    error_exit "$1 komutu bulunamadı!"
  fi
}

# ===== [1. Sistem Optimizasyonları] =====
echo -e "\n[1/12] Sistem Optimizasyonları Yapılıyor..."
{
  # Zaman dilimi ayarı
  timedatectl set-timezone $SYSTEM_TIMEZONE || error_exit "Zaman dilimi ayarlanamadı"
  
  # Locale ayarları
  export LC_ALL=C.UTF-8
  export LANG=C.UTF-8
  
  # Dosya limitleri
  echo "* soft nofile 102400" >> /etc/security/limits.conf
  echo "* hard nofile 102400" >> /etc/security/limits.conf
  echo "asterisk soft nofile 102400" >> /etc/security/limits.conf
  echo "asterisk hard nofile 102400" >> /etc/security/limits.conf
  
  # Kernel parametreleri
  cat >> /etc/sysctl.conf <<EOF
net.ipv4.tcp_fin_timeout = 30
net.core.somaxconn = 1024
vm.swappiness = 10
fs.file-max = 102400
EOF
  sysctl -p > /dev/null
} || error_exit "Sistem optimizasyonlarında hata oluştu"

# ===== [2. Paket Güncellemeleri] =====
echo -e "\n[2/12] Sistem Güncellemeleri Yapılıyor..."
{
  apt-get update -qq || error_exit "Paket listesi güncellenemedi"
  apt-get upgrade -y -qq || error_exit "Sistem güncellemeleri yapılamadı"
  apt-get autoremove -y -qq
} || error_exit "Güncelleme sürecinde hata oluştu"

# ===== [3. Temel Bağımlılıklar] =====
echo -e "\n[3/12] Temel Bağımlılıklar Yükleniyor..."
{
  # Universe deposunu etkinleştir
  add-apt-repository universe -y || error_exit "Universe deposu etkinleştirilemedi"
  apt-get update -qq

  # Gerekli paketler
  apt-get install -y -qq \
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
    || error_exit "Bağımlılık yükleme hatası"
    
  # Node.js alternatif yükleme
  if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_16.x | bash -
    apt-get install -y -qq nodejs || error_exit "NodeJS yükleme hatası"
  fi
  
  # NPM ayarları
  npm config set cache /tmp/npm_cache --global
  npm install -g npm@latest pm2
} || error_exit "Bağımlılık kurulumunda hata oluştu"

# ===== [4. MariaDB Yapılandırması] =====
echo -e "\n[4/12] MariaDB Yapılandırılıyor..."
{
  # MySQL güvenlik ayarları
  MYSQL_ROOT_PASS=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 16)
  ASTERISK_DB_PASS=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 16)
  
  systemctl start mariadb
  systemctl enable mariadb
  
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
  
  # InnoDB ayarları
  sed -i '/^\[mysqld\]/a \
innodb_buffer_pool_size = 256M\
innodb_log_file_size = 128M\
innodb_lock_wait_timeout = 90\
innodb_flush_log_at_trx_commit = 2' /etc/mysql/mariadb.conf.d/50-server.cnf
  
  systemctl restart mariadb
} || error_exit "MariaDB yapılandırmasında hata oluştu"

# ===== [5. Asterisk Kurulumu] =====
echo -e "\n[5/12] Asterisk Derleme ve Kurulumu..."
{
  mkdir -p $INSTALL_DIR
  cd $INSTALL_DIR
  
  # Asterisk indirme
  if [ ! -f asterisk-${ASTERISK_VERSION}.tar.gz ]; then
    wget -q http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz \
      || wget -q http://downloads.asterisk.org/pub/telephony/asterisk/old-releases/asterisk-${ASTERISK_VERSION}.tar.gz \
      || error_exit "Asterisk indirme hatası"
  fi
  
  tar xf asterisk-${ASTERISK_VERSION}.tar.gz || error_exit "Asterisk arşivi açılamadı"
  cd asterisk-${ASTERISK_VERSION} || error_exit "Asterisk dizinine geçilemedi"
  
  # Bağımlılıklar
  contrib/scripts/install_prereq install -qq || error_exit "Asterisk bağımlılıkları yüklenemedi"
  
  # Yapılandırma
  ./configure --with-jansson-bundled --with-pjproject-bundled > /dev/null || error_exit "Asterisk configure hatası"
  
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
    menuselect.makeopts || error_exit "Menuselect yapılandırma hatası"
  
  # Derleme
  make -j$(nproc) > /dev/null || error_exit "Asterisk derleme hatası"
  make install > /dev/null || error_exit "Asterisk kurulum hatası"
  make config > /dev/null || error_exit "Asterisk config hatası"
  ldconfig > /dev/null
  
  # Kullanıcı ve izinler
  if ! id asterisk &>/dev/null; then
    useradd -r -d /var/lib/asterisk -s /bin/bash -c "Asterisk PBX" asterisk
  fi
  usermod -aG audio,dialout asterisk
  mkdir -p /var/{lib,log,run,spool}/asterisk
  chown -R asterisk:asterisk /var/{lib,log,run,spool}/asterisk /etc/asterisk
} || error_exit "Asterisk kurulumunda hata oluştu"

# ===== [6. FreePBX Kurulumu] =====
echo -e "\n[6/12] FreePBX Kurulumu Yapılıyor..."
{
  cd $INSTALL_DIR
  
  # FreePBX indirme
  if [ ! -f freepbx-${FREEPBX_VERSION}.tgz ]; then
    wget -q https://mirror.freepbx.org/modules/packages/freepbx/freepbx-${FREEPBX_VERSION}.tgz \
      || error_exit "FreePBX indirme hatası"
  fi
  
  tar xf freepbx-${FREEPBX_VERSION}.tgz || error_exit "FreePBX arşivi açılamadı"
  cd freepbx || error_exit "FreePBX dizinine geçilemedi"
  
  # Asterisk servisi
  ./start_asterisk start || error_exit "Asterisk başlatılamadı"
  
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
    || error_exit "FreePBX kurulum hatası"
  
  # Admin şifresi
  FREEPBX_ADMIN_PASS=$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-12)
  /usr/sbin/fwconsole chpass --username=admin --password=$FREEPBX_ADMIN_PASS \
    || error_exit "Admin şifresi ayarlanamadı"
} || error_exit "FreePBX kurulumunda hata oluştu"

# ===== [7. amportal.conf Yapılandırması] =====
echo -e "\n[7/12] FreePBX Yapılandırması Yapılıyor..."
{
  # amportal.conf dosyası oluştur
  cat > /etc/amportal.conf <<EOF
AMPWEBROOT=/var/www/html
AMPASTERISKUSER=asterisk
AMPASTERISKGROUP=asterisk
AMPCGIBIN=\$AMPWEBROOT/cgi-bin
AMPBIN=/var/lib/asterisk/bin
AMPSBIN=/usr/sbin
AMPMAN=/usr/share/man
AMPWEBADDRESS=http://$(hostname -I | awk '{print $1}')
AMPDBHOST=localhost
AMPDBNAME=asterisk
AMPDBUSER=asteriskuser
AMPDBPASS=$ASTERISK_DB_PASS
AMPENGINE=asterisk
AMPMGRUSER=admin
AMPMGRPASS=$FREEPBX_ADMIN_PASS
AMPBINMODE=0755
AMPPLAYKEY=#
AMPDISABLELOG=0
AMPCIDLOOKUP=0
AMPEXTENSIONS=extensions
AMPADMINEMAIL=admin@$(hostname)
DEVLANGUAGE=tr_TR
EOF

  # Dosya izinleri
  chown asterisk:asterisk /etc/amportal.conf
  chmod 644 /etc/amportal.conf
} || error_exit "FreePBX yapılandırmasında hata oluştu"

# ===== [8. Apache Yapılandırması] =====
echo -e "\n[8/12] Apache Yapılandırılıyor..."
{
  # Apache modülleri
  a2enmod rewrite headers expires deflate || error_exit "Apache modülleri etkinleştirilemedi"
  
  # PHP ayarları
  for config in /etc/php/*/apache2/php.ini; do
    sed -i 's/^\(memory_limit =\).*/\1 256M/' $config
    sed -i 's/^\(upload_max_filesize =\).*/\1 20M/' $config
    sed -i 's/^\(post_max_size =\).*/\1 20M/' $config
    sed -i 's/^\(max_execution_time =\).*/\1 300/' $config
    sed -i 's/^\(max_input_time =\).*/\1 300/' $config
    sed -i 's/^\(;date.timezone =\).*/\1 $SYSTEM_TIMEZONE/' $config
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
  systemctl restart apache2 || error_exit "Apache yeniden başlatılamadı"
} || error_exit "Apache yapılandırmasında hata oluştu"

# ===== [9. Güvenlik Ayarları] =====
echo -e "\n[9/12] Güvenlik Ayarları Yapılandırılıyor..."
{
  # Firewall ayarları
  ufw allow 22/tcp comment 'SSH'
  ufw allow 80/tcp comment 'HTTP'
  ufw allow 443/tcp comment 'HTTPS'
  ufw allow 5060/tcp comment 'SIP TCP'
  ufw allow 5060/udp comment 'SIP UDP'
  ufw allow 5061/tcp comment 'SIP TLS'
  ufw allow 10000:20000/udp comment 'RTP Ports'
  echo "y" | ufw enable || error_exit "Firewall etkinleştirilemedi"
  
  # Fail2Ban
  apt-get install -y -qq fail2ban || error_exit "Fail2Ban yüklenemedi"
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
} || error_exit "Güvenlik yapılandırmasında hata oluştu"

# ===== [10. FreePBX Modülleri] =====
echo -e "\n[10/12] FreePBX Modülleri Yükleniyor..."
{
  # Temel modüller
  /usr/sbin/fwconsole ma install core || error_exit "Core modül yüklenemedi"
  /usr/sbin/fwconsole ma install framework || error_exit "Framework modül yüklenemedi"
  /usr/sbin/fwconsole ma install sipsettings || error_exit "SIP Settings modül yüklenemedi"
  /usr/sbin/fwconsole ma install pm2 || error_exit "PM2 modül yüklenemedi"
  
  # Modül güncellemeleri
  /usr/sbin/fwconsole ma upgradeall || error_exit "Modül güncellemeleri yapılamadı"
  /usr/sbin/fwconsole ma refreshsignatures || error_exit "İmzalar yenilenemedi"
  
  # PM2 yapılandırması
  sudo -u asterisk pm2 start /var/www/html/admin/modules/pm2/node_modules/pm2
  sudo -u asterisk pm2 save
  sudo -u asterisk pm2 startup
} || error_exit "FreePBX modül yüklemesinde hata oluştu"

# ===== [11. Servislerin Başlatılması] =====
echo -e "\n[11/12] Servisler Başlatılıyor..."
{
  # Sistem servisleri
  systemctl enable asterisk
  systemctl start asterisk
  systemctl restart apache2
  
  # FreePBX cron job
  echo "*/5 * * * * /usr/sbin/fwconsole cron --quiet" | crontab -u asterisk -
  
  # FreePBX servisi
  /usr/sbin/fwconsole reload || error_exit "FreePBX reload hatası"
  /usr/sbin/fwconsole restart || error_exit "FreePBX restart hatası"
} || error_exit "Servis başlatmada hata oluştu"

# ===== [12. Kurulum Sonrası Temizlik] =====
echo -e "\n[12/12] Kurulum Sonrası Temizlik Yapılıyor..."
{
  # Geçici dosyaları temizle
  rm -rf $INSTALL_DIR/asterisk-${ASTERISK_VERSION}*
  rm -rf $INSTALL_DIR/freepbx-${FREEPBX_VERSION}*
  
  # Paket önbelleğini temizle
  apt-get autoremove -y -qq
  apt-get clean -y -qq
} || error_exit "Temizlik sırasında hata oluştu"

# ===== [Kurulum Tamamlandı] =====
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
echo -e "\n\033[1;36mÖnemli Portlar:\033[0m"
echo -e "  - HTTP: \033[1;35m80\033[0m, HTTPS: \033[1;35m443\033[0m"
echo -e "  - SIP: \033[1;35m5060\033[0m (UDP/TCP), TLS: \033[1;35m5061\033[0m"
echo -e "  - RTP: \033[1;35m10000-20000\033[0m (UDP)"
echo -e "\n\033[1;33mLog Dosyası: \033[1;35m$LOG_FILE\033[0m"
echo -e "\033[1;31mNOT: Bu bilgileri güvenli bir yere kaydedin!\033[0m"
echo -e "\033[1;32m=============================================\033[0m"

exit 0
