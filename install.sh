#!/bin/bash

set -e

echo "[OK] Sistem güncelleniyor..."
apt update -y
apt install -y python3-pip ffmpeg unzip mpg123 wget

echo "[OK] Python kütüphaneleri yükleniyor..."
pip3 install --upgrade pip
pip3 install vosk fasttext googletrans==4.0.0-rc1 gTTS websockets

echo "[OK] VOSK Türkçe modeli kontrol ediliyor..."
cd /root
if [ ! -d "/root/vosk-model-small-tr-0.3" ]; then
    wget https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip -O vosk-model-small-tr-0.3.zip
    unzip vosk-model-small-tr-0.3.zip
    rm vosk-model-small-tr-0.3.zip
    echo "[OK] VOSK modeli indirildi ve açıldı."
else
    echo "[OK] VOSK modeli zaten mevcut."
fi

echo "[OK] FastText dil modeli kontrol ediliyor..."
if [ ! -f "/root/lid.176.bin" ]; then
    wget https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin -O /root/lid.176.bin
    echo "[OK] lid.176.bin indirildi."
else
    echo "[OK] lid.176.bin zaten mevcut."
fi

echo "[OK] Kurulum tamamlandı."
echo "Kullanım: python3 backendautox.py --url \"https://canli-yayin-linki.m3u8\""
