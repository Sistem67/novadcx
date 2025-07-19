#!/bin/bash
# Ubuntu 22.04 için Tam Otomatik FreePBX Kurulum Scripti
# Universe Deposu Sorunlarına Özel Çözümler İçerir

# === [KONFİGÜRASYON] ===
LOG_FILE="/var/log/freepbx_install_$(date +%Y%m%d_%H%M%S).log"

# === [FONKSİYONLAR] ===
function die {
  echo -e "\033[1;31mHATA: $1\033[0m" | tee -a "$LOG_FILE"
  exit 1
}

function enable_universe {
  echo ">>> Universe deposu etkinleştiriliyor..." | tee -a "$LOG_FILE"
  
  # 1. Geleneksel yöntem
  add-apt-repository universe -y &>> "$LOG_FILE" && return 0
  
  # 2. Manuel etkinleştirme
  echo ">>> Alternatif yöntem deneyerek universe deposu etkinleştiriliyor..." | tee -a "$LOG_FILE"
  sed -i '/^# deb.*universe/s/^# //' /etc/apt/sources.list
  
  # 3. Eski stil etkinleştirme
  echo "deb http://archive.ubuntu.com/ubuntu $(lsb_release -sc) universe" >> /etc/apt/sources.list
  
  # 4. Güvenli kontrol
  if ! grep -q "universe" /etc/apt/sources.list; then
    die "Universe deposu etkinleştirilemedi!"
  fi
  
  apt-get update -y &>> "$LOG_FILE" || die "Depo güncellemesi başarısız"
  echo ">>> Universe deposu başarıyla etkinleştirildi" | tee -a "$LOG_FILE"
}

# === [KURULUM ÖNCESİ KONTROLLER] ===
echo -e "\n\033[1;36mFreePBX Kurulum Başlatılıyor...\033[0m" | tee -a "$LOG_FILE"

# Root kontrolü
[ "$(id -u)" -eq 0 ] || die "Bu script root olarak çalıştırılmalıdır!"

# Internet bağlantı kontrolü
ping -c 1 google.com &>> "$LOG_FILE" || die "Internet bağlantısı yok!"

# Ubuntu versiyon kontrolü
grep -q "Ubuntu 22.04" /etc/os-release || die "Sadece Ubuntu 22.04 desteklenmektedir!"

# === [1. SİSTEM GÜNCELLEMELERİ] ===
echo -e "\n[1/6] Sistem Güncellemeleri Yapılıyor..." | tee -a "$LOG_FILE"
enable_universe
apt-get update -y &>> "$LOG_FILE" || die "Paket listesi güncellenemedi"
apt-get upgrade -y &>> "$LOG_FILE" || die "Sistem güncellemeleri yapılamadı"

# === [2. TEMEL BAĞIMLILIKLAR] ===
echo -e "\n[2/6] Temel Bağımlılıklar Yükleniyor..." | tee -a "$LOG_FILE"
apt-get install -y software-properties-common &>> "$LOG_FILE" || die "software-properties-common yüklenemedi"
enable_universe

# Gerekli paketler
REQUIRED_PKGS=(
  wget curl git build-essential
  apache2 mariadb-server php
  libjansson-dev libsqlite3-dev
  libxml2-dev libiksemel-dev
  nodejs npm fail2ban
)

for pkg in "${REQUIRED_PKGS[@]}"; do
  echo ">>> $pkg yükleniyor..." | tee -a "$LOG_FILE"
  apt-get install -y "$pkg" &>> "$LOG_FILE" || die "$pkg yüklenemedi"
done

# === [3. ASTERISK KURULUMU] ===
echo -e "\n[3/6] Asterisk Kurulumu Yapılıyor..." | tee -a "$LOG_FILE"
wget http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-20-current.tar.gz -O /tmp/asterisk.tar.gz &>> "$LOG_FILE" || die "Asterisk indirilemedi"
tar xf /tmp/asterisk.tar.gz -C /tmp/ &>> "$LOG_FILE" || die "Asterisk arşivi açılamadı"
cd /tmp/asterisk-*/ || die "Asterisk dizinine geçilemedi"

# Bağımlılıklar
contrib/scripts/install_prereq install &>> "$LOG_FILE" || die "Asterisk bağımlılıkları yüklenemedi"

# Derleme ve kurulum
./configure &>> "$LOG_FILE" || die "Yapılandırma hatası"
make -j$(nproc) &>> "$LOG_FILE" || die "Derleme hatası"
make install &>> "$LOG_FILE" || die "Kurulum hatası"
make config &>> "$LOG_FILE" || die "Yapılandırma hatası"

# === [4. FREEPBX KURULUMU] ===
echo -e "\n[4/6] FreePBX Kurulumu Yapılıyor..." | tee -a "$LOG_FILE"
wget https://mirror.freepbx.org/modules/packages/freepbx/freepbx-16.0-latest.tgz -O /tmp/freepbx.tgz &>> "$LOG_FILE" || die "FreePBX indirilemedi"
tar xf /tmp/freepbx.tgz -C /tmp/ &>> "$LOG_FILE" || die "FreePBX arşivi açılamadı"
cd /tmp/freepbx/ || die "FreePBX dizinine geçilemedi"

# Kurulum
./start_asterisk start &>> "$LOG_FILE" || die "Asterisk başlatılamadı"
./install -n &>> "$LOG_FILE" || die "FreePBX kurulumu başarısız"

# === [5. YAPILANDIRMA] ===
echo -e "\n[5/6] Sistem Yapılandırması Yapılıyor..." | tee -a "$LOG_FILE"

# Apache ayarları
a2enmod rewrite &>> "$LOG_FILE" || die "Apache modül etkinleştirilemedi"
systemctl restart apache2 &>> "$LOG_FILE" || die "Apache yeniden başlatılamadı"

# Firewall ayarları
ufw allow 80/tcp &>> "$LOG_FILE"
ufw allow 5060/udp &>> "$LOG_FILE"
ufw allow 10000:20000/udp &>> "$LOG_FILE"

# === [6. SERVİSLERİN BAŞLATILMASI] ===
echo -e "\n[6/6] Servisler Başlatılıyor..." | tee -a "$LOG_FILE"
systemctl enable asterisk &>> "$LOG_FILE" || die "Asterisk servisi etkinleştirilemedi"
systemctl start asterisk &>> "$LOG_FILE" || die "Asterisk başlatılamadı"

# === [KURULUM TAMAMLANDI] ===
IP_ADDR=$(hostname -I | awk '{print $1}')
echo -e "\n\033[1;32m===== KURULUM BAŞARIYLA TAMAMLANDI =====\033[0m" | tee -a "$LOG_FILE"
echo -e "FreePBX Paneli: http://$IP_ADDR" | tee -a "$LOG_FILE"
echo -e "Kullanıcı Adı: admin" | tee -a "$LOG_FILE"
echo -e "Şifre: admin (ilk girişte değiştirin)" | tee -a "$LOG_FILE"
echo -e "\nDetaylı log için: $LOG_FILE" | tee -a "$LOG_FILE"

exit 0
