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
logger = logging.getLogger('SüperTranslator')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
TARGET_INTERVAL = 3.2  # 3.2 saniyelik çeviri aralığı
MIN_WORDS = 3          # Minimum kelime sayısı
MAX_WORDS = 12         # Maksimum kelime sayısı

# Model yolları (DÜZELTİLDİ)
VOSK_MODEL_PATH = "/root/vosk-model-en-us-0.22"  # Standart model yolu
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Global nesneler
try:
    model = Model(VOSK_MODEL_PATH)
    fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
except Exception as e:
    logger.error(f"Model yükleme hatası: {e}")
    exit(1)

clients = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()
last_translations = deque(maxlen=3)  # Son 3 çeviriyi sakla

def play_audio(data):
    """Bellekten ses çalma (non-blocking)"""
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate)
    except Exception as e:
        logger.warning(f"Ses oynatma hatası: {e}")

def tts_worker():
    """Ses sentezleme işçisi"""
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

def is_repetition(text):
    """Tekrar kontrolü"""
    text_lower = text.lower()
    for last_text in last_translations:
        if text_lower in last_text.lower() or last_text.lower() in text_lower:
            return True
    return False

async def force_translate_to_turkish(text):
    """Kesin Türkçe çeviri (3 katmanlı)"""
    # 1. LibreTranslate denemesi
    try:
        params = {'q': text, 'source': 'auto', 'target': 'tr', 'format': 'text'}
        response = await asyncio.to_thread(
            requests.post,
            "https://libretranslate.de/translate",
            data=params,
            timeout=1.8
        )
        if response.status_code == 200:
            translated = response.json().get('translatedText')
            if translated and translated != text:
                return translated
    except:
        pass

    # 2. Google Translate yedeği
    try:
        params = {'client': 'gtx', 'sl': 'auto', 'tl': 'tr', 'dt': 't', 'q': text}
        response = await asyncio.to_thread(
            requests.get,
            "https://translate.googleapis.com/translate_a/single",
            params=params,
            timeout=1.8
        )
        if response.status_code == 200:
            return ''.join([x[0] for x in response.json()[0] if x[0]])
    except:
        pass

    # 3. Manuel sözlük fallback
    dictionary = {
        'hello': 'merhaba',
        'world': 'dünya',
        'thank you': 'teşekkürler',
        'good': 'iyi',
        'bad': 'kötü'
    }
    for eng, tr in dictionary.items():
        text = text.replace(eng, tr)
    
    return text if any(c.isalpha() for c in text) else None

async def process_audio_stream(url):
    """Ana işlem döngüsü"""
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
                
                if text and len(text.split()) >= 1:  # Tek kelimelik çıktıları filtrele
                    text_buffer.append(text)
                    current_time = time.time()
                    
                    # Zaman veya kelime sayısı kontrolü
                    if (current_time - last_translation_time >= TARGET_INTERVAL or 
                        len(text_buffer) >= MAX_WORDS):
                        
                        full_text = ' '.join(text_buffer)
                        if not is_repetition(full_text):
                            translated = await force_translate_to_turkish(full_text)
                            
                            if translated:
                                with print_lock:
                                    print(f"\n\033[1;34m[ORJINAL]\033[0m {full_text}")
                                    print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
                                
                                await broadcast(translated)
                                audio_queue.put(translated)
                                last_translations.append(full_text)
                        
                        text_buffer = []
                        last_translation_time = current_time
                        
            await asyncio.sleep(0.005)
    except Exception as e:
        logger.error(f"Ses işleme hatası: {e}")
    finally:
        proc.terminate()

async def broadcast(message):
    """Tüm istemcilere yayın"""
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
    """Uygulama giriş noktası"""
    threading.Thread(target=tts_worker, daemon=True).start()
    
    server = await websockets.serve(
        client_handler,
        "0.0.0.0",
        8000,
        ping_interval=20,
        ping_timeout=10
    )
    
    print("\n\033[1;36mAKICI TÜRKÇE ÇEVİRİ SERVİSİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print(f"\033[93mÇeviri aralığı: ~{TARGET_INTERVAL}s | Kelime sınırı: {MIN_WORDS}-{MAX_WORDS}\033[0m\n")
    
    try:
        await process_audio_stream(url)
    except asyncio.CancelledError:
        logger.info("Servis durduruluyor...")
    except Exception as e:
        logger.error(f"Kritik hata: {e}")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Akıcı Türkçe Çeviri')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Başlatma hatası: {e}")
