#!/bin/bash
# pbx.sh - Asterisk kurulumu

# Kök kontrolü
if [ "$(id -u)" -ne 0 ]; then
  echo "Bu script root olarak çalıştırılmalıdır!" >&2
  exit 1
fi

# Gerekli bağımlılıklar
echo "Asterisk için bağımlılıklar kuruluyor..."
apt-get install -y libxml2-dev libncurses5-dev uuid-dev libjansson-dev libsqlite3-dev libssl-dev

# Asterisk indirme ve kurulum
echo "Asterisk indiriliyor ve kuruluyor..."
cd /usr/src
wget http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-18-current.tar.gz
tar -xzvf asterisk-18-current.tar.gz
rm -f asterisk-18-current.tar.gz
cd asterisk-18.*

# Asterisk yapılandırması
echo "Asterisk yapılandırılıyor..."
contrib/scripts/install_prereq install
./configure --with-jansson-bundled

# Derleme ve kurulum
echo "Asterisk derleniyor ve kuruluyor..."
make menuselect.makeopts
menuselect/menuselect --enable-category MENUSELECT_ADDONS \
                      --enable app_mysql \
                      --enable cdr_mysql \
                      --enable res_config_mysql \
                      --enable format_mp3 \
                      --enable res_http_websocket \
                      --enable res_phoneprov \
                      menuselect.makeopts

make -j4 && make install && make samples && make config && make basic-pbx

# Kullanıcı ve grup ayarları
echo "Asterisk kullanıcı ve grup ayarları yapılıyor..."
useradd -m -d /var/lib/asterisk -r -c "Asterisk PBX" -s /bin/false asterisk
chown -R asterisk:asterisk /var/lib/asterisk /var/spool/asterisk /var/log/asterisk /var/run/asterisk /etc/asterisk
usermod -aG audio,dialout asterisk

# Systemd servis dosyası
echo "[Unit]
Description=Asterisk PBX
After=network.target

[Service]
Type=simple
User=asterisk
Group=asterisk
ExecStart=/usr/sbin/asterisk -f -C /etc/asterisk/asterisk.conf
ExecReload=/usr/sbin/asterisk -rx 'core reload'
Restart=always

[Install]
WantedBy=multi-user.target" > /etc/systemd/system/asterisk.service

systemctl daemon-reload
systemctl enable asterisk
systemctl start asterisk

echo "Asterisk kurulumu tamamlandı! Şimdi pbx.freepbx scriptini çalıştırabilirsiniz."
