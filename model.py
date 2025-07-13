#!/usr/bin/env python3
import argparse
import asyncio
import io
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from queue import Queue

import fasttext
import requests
import sounddevice as sd
import soundfile as sf
import websockets
from gtts import gTTS
from vosk import KaldiRecognizer, Model

# Sistem optimizasyonları
os.nice(15)
os.environ['OMP_NUM_THREADS'] = '1'

# Logging konfigürasyonu
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('TRTranslator')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
BUFFER_SECONDS = 3.5  # 3.5 saniyelik buffer
MIN_WORDS = 3         # Minimum kelime sayısı
MAX_WORDS = 10        # Maksimum kelime sayısı

# Model yolları
VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Global nesneler
try:
    model = Model(VOSK_MODEL_PATH)
    fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
except Exception as e:
    logger.error(f"Model yükleme hatası: {e}")
    raise

clients = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()

def play_audio(data):
    """Bellekten ses çal"""
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate)
    except Exception as e:
        logger.warning(f"Ses oynatma hatası: {e}")

def tts_worker():
    """Ses üretim işçisi"""
    while True:
        text = audio_queue.get()
        try:
            with io.BytesIO() as f:
                tts = gTTS(text=text, lang='tr', lang_check=False, slow=False)
                tts.write_to_fp(f)
                play_audio(f.getvalue())
        except Exception as e:
            logger.error(f"TTS hatası: {e}")
        finally:
            audio_queue.task_done()

def clean_text(text):
    """Metin temizleme"""
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)

async def force_translate_to_turkish(text):
    """Kesin Türkçe çeviri (source=auto ama target her zaman tr)"""
    try:
        # LibreTranslate API
        params = {
            'q': text,
            'source': 'auto',  # Otomatik dil algılama
            'target': 'tr',     # HER ZAMAN Türkçe'ye çevir
            'format': 'text'
        }
        response = await asyncio.to_thread(
            requests.post,
            "https://libretranslate.de/translate",
            data=params,
            timeout=1.5
        )
        if response.status_code == 200:
            return response.json().get('translatedText', text)
    except Exception as e:
        logger.debug(f"Çeviri hatası: {e}")
    
    return text  # Fallback

async def process_audio_stream(url):
    """Ses işleme döngüsü"""
    cmd = [
        'ffmpeg', '-i', url, '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-'
    ]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    
    text_buffer = []
    last_translation_time = time.time()
    
    try:
        while True:
            data = proc.stdout.read(CHUNK_SIZE)
            if not data:
                await asyncio.sleep(0.01)
                continue
                
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get('text', '').strip()
                
                if text:
                    text_buffer.append(text)
                    current_time = time.time()
                    
                    # Buffer dolduğunda veya zaman aşımı olduğunda çevir
                    if (len(text_buffer) >= MIN_WORDS and 
                        (len(text_buffer) >= MAX_WORDS or 
                         current_time - last_translation_time >= BUFFER_SECONDS)):
                        
                        clean_text = ' '.join(text_buffer)
                        translated = await force_translate_to_turkish(clean_text)
                        
                        with print_lock:
                            print(f"\n\033[1;34m[ORJINAL]\033[0m {clean_text}")
                            print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
                        
                        await broadcast(translated)
                        audio_queue.put(translated)
                        
                        text_buffer = []
                        last_translation_time = current_time
                        
            await asyncio.sleep(0.005)
    finally:
        proc.terminate()

async def broadcast(message):
    """Tüm istemcilere gönder"""
    if clients:
        await asyncio.wait([ws.send(message) for ws in clients], timeout=0.5)

async def client_handler(ws, path):
    """WebSocket yönetimi"""
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.remove(ws)

async def main(url):
    """Ana uygulama"""
    threading.Thread(target=tts_worker, daemon=True).start()
    
    server = await websockets.serve(
        client_handler,
        "0.0.0.0",
        8000,
        ping_interval=20,
        ping_timeout=10
    )
    
    print("\n\033[1;36mTÜRKÇE ÇEVİRİ SERVİSİ (Tüm diller → TR)\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print(f"\033[93mÇeviri aralığı: ~{BUFFER_SECONDS}s | Kelime sınırı: {MIN_WORDS}-{MAX_WORDS}\033[0m\n")
    
    try:
        await process_audio_stream(url)
    except asyncio.CancelledError:
        logger.info("Servis durduruluyor...")
    except Exception as e:
        logger.error(f"Hata: {e}")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Tüm Dilleri Türkçeye Çevir')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Kritik hata: {e}")
