#!/bin/bash

set -e

# Basit çıktı
echo "[OK] Gerekli paketler kuruluyor..."
apt update -y
apt install -y build-essential cmake git python3-pip ffmpeg wget unzip

cd /root

# Whisper.cpp indir
if [ ! -d whisper.cpp ]; then
  echo "[OK] Whisper.cpp klonlanıyor..."
  git clone https://github.com/ggerganov/whisper.cpp
else
  echo "[OK] Whisper.cpp klasörü zaten var."
fi

cd whisper.cpp

# Derleme
echo "[OK] Whisper.cpp derleniyor..."
make

# Model indir
echo "[OK] Model indiriliyor (base)..."
./models/download-ggml-model.sh base

# Test çalıştır
echo "[OK] Test dosyası çalıştırılıyor (jfk.wav)..."
./main -m models/ggml-base.bin -f samples/jfk.wav || echo "[UYARI] Test sesi çalışmadı ama derleme tamam."

# Bilgilendirme
echo "[OK] Whisper.cpp kurulumu tamamlandı."
echo "Kullanım örneği:"
echo "cd /root/whisper.cpp"
echo "./main -m models/ggml-base.bin -f samples/jfk.wav"
