#!/bin/bash
# FreePBX Ultimate Auto-Install Script (v3.0)
# Ubuntu 22.04 LTS için tam otomatik, hatasız kurulum
# Tüm bağımlılıklar, güvenlik ayarları ve optimizasyonlar dahil

# ===== [Kurulum Parametreleri] =====
export DEBIAN_FRONTEND=noninteractive
INSTALL_DIR="/opt/freepbx_install"
LOG_FILE="/var/log/freepbx_install.log"
ASTERISK_VERSION="20.2.0"
FREEPBX_VERSION="16.0.29"
SYSTEM_TIMEZONE="Europe/Istanbul"

# ===== [Başlangıç Kontrolleri] =====
if [ "$(id -u)" -ne 0 ]; then
  echo "Bu script root yetkileriyle çalıştırılmalıdır!" >&2
  exit 1
fi

mkdir -p $INSTALL_DIR
exec > >(tee -a $LOG_FILE) 2>&1
echo -e "\n\n===== [FreePBX Kurulum Başlangıç: $(date)] ====="

# ===== [Sistem Optimizasyonları] =====
echo -e "\n[1/12] Sistem Optimizasyonları Yapılıyor..."
{
  # Zaman dilimi ayarı
  timedatectl set-timezone $SYSTEM_TIMEZONE
  
  # Performans ayarları
  echo "net.ipv4.tcp_fin_timeout = 30" >> /etc/sysctl.conf
  echo "net.core.somaxconn = 1024" >> /etc/sysctl.conf
  echo "vm.swappiness = 10" >> /etc/sysctl.conf
  sysctl -p
  
  # Dosya açma limiti
  echo "* soft nofile 102400" >> /etc/security/limits.conf
  echo "* hard nofile 102400" >> /etc/security/limits.conf
  echo "asterisk soft nofile 102400" >> /etc/security/limits.conf
  echo "asterisk hard nofile 102400" >> /etc/security/limits.conf
  echo "fs.file-max = 102400" >> /etc/sysctl.conf
  sysctl -p
} > /dev/null

# ===== [Sistem Güncellemeleri] =====
echo -e "\n[2/12] Sistem Güncellemeleri Yapılıyor..."
{
  apt-get update -qq
  apt-get upgrade -y -qq
  apt-get autoremove -y -qq
} > /dev/null

# ===== [Temel Bağımlılıklar] =====
echo -e "\n[3/12] Temel Bağımlılıklar Yükleniyor..."
{
  apt-get install -y -qq \
    wget curl git build-essential libssl-dev libncurses5-dev \
    subversion libjansson-dev sqlite3 autoconf automake libtool \
    pkg-config unixodbc unixodbc-dev uuid uuid-dev libasound2-dev \
    libogg-dev libvorbis-dev libcurl4-openssl-dev libical-dev \
    libneon27-dev libsrtp2-dev libspandsp-dev libedit-dev libldap2-dev \
    libmemcached-dev libspeex-dev libspeexdsp-dev libsrtp2-dev \
    libxml2-dev libsqlite3-dev libpq-dev libiksemel-dev libcorosync-dev \
    libnewt-dev libusb-dev libpopt-dev liblua5.2-dev libopus-dev \
    libresample1-dev libvpb-dev sofia-sip-bin
} > /dev/null

# ===== [PHP ve Apache Ayarları] =====
echo -e "\n[4/12] PHP ve Apache Yapılandırması..."
{
  apt-get install -y -qq \
    apache2 mariadb-server php php-mysql php-gd php-curl php-mbstring \
    php-zip php-xml php-json php-cli libapache2-mod-php php-intl \
    php-bcmath php-soap php-ldap php-snmp
    
  # PHP ayarlarını optimize et
  for config in /etc/php/*/apache2/php.ini; do
    sed -i 's/^\(memory_limit =\).*/\1 256M/' $config
    sed -i 's/^\(upload_max_filesize =\).*/\1 20M/' $config
    sed -i 's/^\(post_max_size =\).*/\1 20M/' $config
    sed -i 's/^\(max_execution_time =\).*/\1 300/' $config
    sed -i 's/^\(max_input_time =\).*/\1 300/' $config
    sed -i 's/^\(;date.timezone =\).*/\1 $SYSTEM_TIMEZONE/' $config
  done

  # Apache modülleri
  a2enmod rewrite headers expires
  systemctl restart apache2
} > /dev/null

# ===== [Node.js ve NPM Kurulumu] =====
echo -e "\n[5/12] Node.js ve NPM Kurulumu..."
{
  # NodeSource deposunu ekle
  curl -sL https://deb.nodesource.com/setup_16.x | bash - > /dev/null
  apt-get install -y -qq nodejs
  
  # NPM ayarları
  npm config set cache /tmp/npm_cache --global
  npm install -g npm@latest
  npm install -g pm2
} > /dev/null

# ===== [MariaDB Yapılandırması] =====
echo -e "\n[6/12] MariaDB Yapılandırması..."
{
  # MySQL güvenlik ayarları
  mysql -e "DELETE FROM mysql.user WHERE User='';"
  mysql -e "DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');"
  mysql -e "DROP DATABASE IF EXISTS test;"
  mysql -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';"
  mysql -e "FLUSH PRIVILEGES;"

  # Veritabanı ve kullanıcı oluştur
  MYSQL_ROOT_PASS=$(openssl rand -base64 24)
  ASTERISK_DB_PASS=$(openssl rand -base64 24)
  
  mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '$MYSQL_ROOT_PASS';"
  mysql -uroot -p$MYSQL_ROOT_PASS -e "CREATE DATABASE asterisk;"
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
} > /dev/null

# ===== [Asterisk Kurulumu] =====
echo -e "\n[7/12] Asterisk Derleme ve Kurulumu..."
{
  cd $INSTALL_DIR
  wget -q http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz
  tar xf asterisk-${ASTERISK_VERSION}.tar.gz
  cd asterisk-${ASTERISK_VERSION}
  
  # Bağımlılıkları çöz
  contrib/scripts/install_prereq install -qq
  
  # Yapılandırma ve derleme
  ./configure --with-jansson-bundled --with-pjproject-bundled > /dev/null
  
  # Menuselect optimizasyonları
  make menuselect.makeopts
  menuselect/menuselect \
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
    menuselect.makeopts
  
  make -j$(nproc) > /dev/null
  make install > /dev/null
  make config > /dev/null
  ldconfig > /dev/null
  
  # Asterisk kullanıcısı ve izinleri
  useradd -r -d /var/lib/asterisk -s /bin/bash asterisk
  usermod -aG audio,dialout asterisk
  chown -R asterisk:asterisk /var/{lib,log,run,spool}/asterisk /etc/asterisk
} > /dev/null

# ===== [FreePBX Kurulumu] =====
echo -e "\n[8/12] FreePBX Kurulumu..."
{
  cd $INSTALL_DIR
  wget -q https://mirror.freepbx.org/modules/packages/freepbx/freepbx-${FREEPBX_VERSION}.tgz
  tar xf freepbx-${FREEPBX_VERSION}.tgz
  cd freepbx
  
  # Asterisk'i başlat
  ./start_asterisk start
  
  # FreePBX kurulumu
  ./install -n --dbengine=mysql \
    --dbhost=localhost \
    --dbname=asterisk \
    --dbuser=asteriskuser \
    --dbpass=$ASTERISK_DB_PASS \
    --user=asterisk \
    --group=asterisk \
    --webroot=/var/www/html
  
  # Admin şifresi oluştur
  FREEPBX_ADMIN_PASS=$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-12)
  /usr/sbin/fwconsole chpass --username=admin --password=$FREEPBX_ADMIN_PASS
} > /dev/null

# ===== [amportal.conf Optimizasyonu] =====
echo -e "\n[9/12] amportal.conf Optimizasyonu..."
{
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
AMPBINMODE=0555
AMPPLAYKEY=# 
AMPDISABLELOG=0
AMPCIDLOOKUP=0
AMPEXTENSIONS=extensions
AMPADMINEMAIL=admin@$(hostname)
DEVLANGUAGE=tr_TR
EOF
} > /dev/null

# ===== [Güvenlik Ayarları] =====
echo -e "\n[10/12] Güvenlik Ayarları Yapılandırılıyor..."
{
  # Firewall yapılandırması
  ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 5060/udp
  ufw allow 5061/tcp
  ufw allow 10000:20000/udp
  ufw --force enable
  
  # Fail2Ban kurulumu
  apt-get install -y -qq fail2ban
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
  systemctl start fail2ban
  
  # Dosya izinleri
  chown -R asterisk:asterisk /var/www/html
  find /var/www/html -type d -exec chmod 755 {} \;
  find /var/www/html -type f -exec chmod 644 {} \;
  chmod -R 775 /var/www/html/admin/modules
} > /dev/null

# ===== [FreePBX Optimizasyonları] =====
echo -e "\n[11/12] FreePBX Optimizasyonları Uygulanıyor..."
{
  # FreePBX modül güncellemeleri
  /usr/sbin/fwconsole ma upgradeall
  /usr/sbin/fwconsole ma refreshsignatures
  /usr/sbin/fwconsole reload
  
  # Temel modüllerin kurulumu
  /usr/sbin/fwconsole ma install certman sipsettings webrtc pm2 asteriskinfo
  
  # PM2 yapılandırması
  sudo -u asterisk pm2 start /var/www/html/admin/modules/pm2/node_modules/pm2
  sudo -u asterisk pm2 save
  sudo -u asterisk pm2 startup
} > /dev/null

# ===== [Sistem Servisleri] =====
echo -e "\n[12/12] Sistem Servisleri Başlatılıyor..."
{
  systemctl enable asterisk
  systemctl start asterisk
  systemctl restart apache2
  
  # FreePBX cron job
  echo "*/5 * * * * /usr/sbin/fwconsole cron --quiet" | crontab -u asterisk -
} > /dev/null

# ===== [Kurulum Tamamlandı] =====
IP_ADDR=$(hostname -I | awk '{print $1}')
echo -e "\n\n===== [FREEPBX KURULUMU TAMAMLANDI] ====="
echo -e "Erişim Bilgileri:"
echo -e "  - Web Arayüz: http://$IP_ADDR"
echo -e "  - Kullanıcı Adı: admin"
echo -e "  - Şifre: $FREEPBX_ADMIN_PASS"
echo -e "\nVeritabanı Bilgileri:"
echo -e "  - MySQL Root Şifre: $MYSQL_ROOT_PASS"
echo -e "  - Asterisk DB Kullanıcı: asteriskuser"
echo -e "  - Asterisk DB Şifre: $ASTERISK_DB_PASS"
echo -e "\nÖnemli Portlar:"
echo -e "  - HTTP: 80, HTTPS: 443"
echo -e "  - SIP: 5060 (UDP/TCP), TLS: 5061"
echo -e "  - RTP: 10000-20000 (UDP)"
echo -e "\nLog Dosyası: $LOG_FILE"
echo -e "========================================"

# Temizlik
rm -rf $INSTALL_DIR/asterisk-*
rm -rf $INSTALL_DIR/freepbx-*
exit 0
