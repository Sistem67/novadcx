#!/bin/bash
set -e

echo "=== Sistem güncelleniyor ==="
sudo apt update && sudo apt upgrade -y

echo "=== Gerekli paketler kuruluyor (Python, ffmpeg, build araçları) ==="
sudo apt install -y python3 python3-venv python3-pip ffmpeg build-essential wget unzip git

echo "=== Proje klasörü oluşturuluyor: xdcx ==="
mkdir -p xdcx
cd xdcx

if [ ! -d "venv-" ]; then
  echo "=== Python sanal ortamı oluşturuluyor (venv-) ==="
  python3 -m venv venv-
fi

echo "=== Sanal ortam aktif ediliyor ==="
source venv-/bin/activate

echo "=== pip güncelleniyor ==="
pip install --upgrade pip

echo "=== Python bağımlılıkları kuruluyor ==="
pip install \
  vosk \
  fasttext \
  transformers \
  sentencepiece \
  torch \
  uvicorn \
  fastapi \
  python-socketio[asyncio_client] \
  websockets \
  piper-tts

echo "=== FastText dil modeli indiriliyor (lid.176.bin) ==="
if [ ! -f "lid.176.bin" ]; then
  wget -q https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
fi

echo "=== Vosk Türkçe modeli indiriliyor (vosk-model-small-tr-0.15) ==="
if [ ! -d "vosk-model-small-tr-0.15" ]; then
  wget -qO- https://alphacephei.com/vosk/models/vosk-model-small-tr-0.15.zip | busybox unzip -o -d .
fi

echo "=== Kurulum tamamlandı! ==="
echo "Çalıştırmak için:"
echo "cd xdcx && source venv-/bin/activate"
echo "python3 backend.py --url \"https://yayınlinkiniz.m3u8\""
