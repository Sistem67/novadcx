#!/bin/bash
# pbx.freepbx - FreePBX kurulumu

# Kök kontrolü
if [ "$(id -u)" -ne 0 ]; then
  echo "Bu script root olarak çalıştırılmalıdır!" >&2
  exit 1
fi

# FreePBX bağımlılıkları
echo "FreePBX için bağımlılıklar kuruluyor..."
apt-get install -y composer sox mpg123 sqlite3 libicu-dev libtool automake autoconf

# Pear ayarları
echo "Pear ayarları yapılıyor..."
pear channel-update pear.php.net
pear install Console_Getopt

# FreePBX indirme ve kurulum
echo "FreePBX indiriliyor ve kuruluyor..."
cd /usr/src
wget http://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz
tar -xzvf freepbx-16.0-latest.tgz
rm -f freepbx-16.0-latest.tgz
cd freepbx

# FreePBX yapılandırması
echo "FreePBX yapılandırılıyor..."
./start_asterisk start
./install -n --dbengine=mysql --dbname=asterisk --dbuser=root --dbpass=pbxpassword123 \
          --user=asterisk --group=asterisk --webroot=/var/www/html

# amportal.conf sorunlarını önlemek için ayarlar
echo "amportal.conf ayarları yapılıyor..."
sed -i "s/\(AMPDBUSER\s*=\s*\).*/\1'root'/" /etc/amportal.conf
sed -i "s/\(AMPDBPASS\s*=\s*\).*/\1'pbxpassword123'/" /etc/amportal.conf
sed -i "s/\(AMPENGINE\s*=\s*\).*/\1'asterisk'/" /etc/amportal.conf
sed -i "s/\(AMPMGRUSER\s*=\s*\).*/\1'admin'/" /etc/amportal.conf
sed -i "s/\(AMPMGRPASS\s*=\s*\).*/\1'admin123'/" /etc/amportal.conf

# Apache yapılandırması
echo "Apache yapılandırması yapılıyor..."
a2enmod rewrite
a2enmod headers
a2enmod ssl

echo "<VirtualHost *:80>
    DocumentRoot /var/www/html
    <Directory /var/www/html>
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
    ErrorLog \${APACHE_LOG_DIR}/error.log
    CustomLog \${APACHE_LOG_DIR}/access.log combined
</VirtualHost>" > /etc/apache2/sites-available/000-default.conf

systemctl restart apache2

# FreePBX modülleri yükleme
echo "Temel FreePBX modülleri yükleniyor..."
fwconsole ma download core
fwconsole ma install core
fwconsole ma download framework
fwconsole ma install framework
fwconsole ma download sipsettings
fwconsole ma install sipsettings
fwconsole ma download userman
fwconsole ma install userman
fwconsole ma download dashboard
fwconsole ma install dashboard
fwconsole ma download recordings
fwconsole ma install recordings

# FreePBX yeniden başlatma
echo "FreePBX yeniden başlatılıyor..."
fwconsole reload
fwconsole restart

# Kullanıcı bilgileri
echo ""
echo "########################################################"
echo "# FreePBX Kurulumu Tamamlandı!"
echo "#"
echo "# Yönetim Paneli Bilgileri:"
echo "# URL: http://sunucu-ip-adresi"
echo "# Kullanıcı Adı: admin"
echo "# Şifre: admin123"
echo "#"
echo "# MySQL Bilgileri:"
echo "# Kullanıcı Adı: root"
echo "# Şifre: pbxpassword123"
echo "#"
echo "# Asterisk CLI'ye erişmek için: fwconsole cli"
echo "########################################################"
echo ""
