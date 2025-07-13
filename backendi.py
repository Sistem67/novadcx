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
logger = logging.getLogger('AkıcıTranslator')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 2000  # Daha küçük chunk'lar
TARGET_INTERVAL = 3.5  # 3.5 saniyelik hedef aralık
MIN_WORDS = 4       # Minimum kelime sayısı
MAX_WORDS = 10      # Maksimum kelime sayısı
REPEAT_THRESHOLD = 0.7  # Tekrar önleme eşiği

# Model yolları
VOSK_MODEL_PATH = "/root/vosk-model-en-us-0.22-lgraph"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Global nesneler
try:
    model = Model(VOSK_MODEL_PATH)
    fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
except Exception as e:
    logger.error(f"Model yükleme hatası: {e}")
    exit(1)

clients = set()
audio_queue = Queue(maxsize=2)  # Daha küçük kuyruk
print_lock = threading.Lock()
last_translations = deque(maxlen=5)  # Son çevirileri tut

def play_audio(data):
    """Non-blocking ses çalma"""
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate, blocking=False)
    except Exception as e:
        logger.warning(f"Ses oynatma hatası: {e}")

def tts_worker():
    """Optimize TTS işçisi"""
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

def is_similar(text1, text2):
    """Benzerlik kontrolü"""
    words1 = set(text1.split())
    words2 = set(text2.split())
    intersection = words1 & words2
    return len(intersection) / max(len(words1), len(words2)) > REPEAT_THRESHOLD

async def translate_with_retry(text):
    """Hızlı çeviri fonksiyonu"""
    endpoints = [
        ("https://libretranslate.de/translate", "post", {'q': text, 'source': 'auto', 'target': 'tr'}),
        ("https://translate.googleapis.com/translate_a/single", "get", {'client': 'gtx', 'sl': 'auto', 'tl': 'tr', 'dt': 't', 'q': text})
    ]
    
    for url, method, params in endpoints:
        try:
            if method == "post":
                response = await asyncio.to_thread(requests.post, url, data=params, timeout=1.5)
            else:
                response = await asyncio.to_thread(requests.get, url, params=params, timeout=1.5)
            
            if response.status_code == 200:
                if "googleapis" in url:
                    return ''.join([x[0] for x in response.json()[0] if x[0]])
                return response.json().get('translatedText', text)
        except:
            continue
    
    return text  # Fallback

async def process_chunk(text):
    """Metin işleme pipeline'ı"""
    # Temizleme
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text.split()) < MIN_WORDS:
        return None
    
    # Tekrar kontrolü
    for last_text in last_translations:
        if is_similar(text, last_text):
            return None
    
    # Çeviri
    translated = await translate_with_retry(text)
    if not translated or translated == text:
        return None
    
    # Kaydet ve döndür
    last_translations.append(text)
    return translated

async def audio_processor(url):
    """Optimize ses işleme"""
    cmd = [
        'ffmpeg', '-i', url, '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-'
    ]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    
    buffer = []
    last_time = time.time()
    
    try:
        while True:
            data = proc.stdout.read(CHUNK_SIZE)
            if recognizer.AcceptWaveform(data):
                text = json.loads(recognizer.Result()).get('text', '').strip()
                if text:
                    buffer.append(text)
                    current_time = time.time()
                    
                    # Zaman veya kelime sayısı kontrolü
                    if (current_time - last_time >= TARGET_INTERVAL or 
                        len(buffer) >= MAX_WORDS):
                        
                        full_text = ' '.join(buffer)
                        if translated := await process_chunk(full_text):
                            with print_lock:
                                print(f"\n\033[34m[ORJINAL]\033[0m {full_text}")
                                print(f"\033[32m[ÇEVİRİ]\033[0m {translated}\n")
                            
                            await broadcast(translated)
                            audio_queue.put(translated)
                        
                        buffer = []
                        last_time = current_time
            
            await asyncio.sleep(0.005)
    finally:
        proc.terminate()

async def broadcast(message):
    """İstemcilere yayın"""
    if clients:
        await asyncio.wait([ws.send(message) for ws in clients], timeout=0.3)

async def client_handler(ws, path):
    """WebSocket yönetimi"""
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.remove(ws)

async def main(url):
    threading.Thread(target=tts_worker, daemon=True).start()
    
    server = await websockets.serve(
        client_handler,
        "0.0.0.0",
        8000,
        ping_interval=15,
        ping_timeout=8
    )
    
    print("\n\033[36mAKICI ÇEVİRİ AKTİF (3-4s aralıklarla)\033[0m")
    print(f"\033[90mKaynak: {url}\033[0m\n")
    
    try:
        await audio_processor(url)
    except asyncio.CancelledError:
        logger.info("Durduruluyor...")
    except Exception as e:
        logger.error(f"Hata: {e}")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Akıcı Çeviri Servisi')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Başlatma hatası: {e}")
