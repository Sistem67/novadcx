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
from functools import lru_cache
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
os.environ['PYTHONUNBUFFERED'] = '1'

# Logging konfigürasyonu
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('SmoothTranslator')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 2000  # Daha küçük chunk'lar
INITIAL_BUFFER_TIME = 10  # İlk 10 saniyelik buffer
REALTIME_INTERVAL = 3.5  # 3-4 saniyelik doğal aralık
MIN_WORDS = 5  # Minimum kelime sayısı
MAX_WORDS = 15  # Maksimum kelime sayısı

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
audio_queue = Queue(maxsize=3)  # Küçük queue
print_lock = threading.Lock()
audio_buffer = deque(maxlen=int(SAMPLE_RATE*INITIAL_BUFFER_TIME/CHUNK_SIZE))

def play_audio(data):
    """Bellekten kesintisiz ses çal"""
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate, blocking=False)
    except Exception as e:
        logger.warning(f"Ses oynatma hatası: {e}")

def tts_worker():
    """Kesintisiz TTS işçisi"""
    while True:
        text = audio_queue.get()
        try:
            with io.BytesIO() as f:
                tts = gTTS(text=text, lang='tr', lang_check=False)
                tts.write_to_fp(f)
                play_audio(f.getvalue())
        except Exception as e:
            logger.error(f"TTS hatası: {e}")
        finally:
            audio_queue.task_done()

@lru_cache(maxsize=500)
def clean_text(text):
    """Hızlı metin temizleme"""
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)

def detect_language(text):
    """Hızlı dil tespiti"""
    try:
        if len(text.split()) < 3:
            return 'en'
        pred = fasttext_model.predict(text, k=1)
        return pred[0][0].replace('__label__', '') if pred[1][0] >= 0.4 else 'en'
    except:
        return 'en'

async def translate_to_turkish(text):
    """Hızlı çeviri servisi"""
    try:
        params = {'q': text, 'source': 'auto', 'target': 'tr', 'format': 'text'}
        resp = await asyncio.to_thread(requests.post, 
                                     "https://libretranslate.de/translate",
                                     data=params, timeout=1.5)
        return resp.json().get('translatedText', text) if resp.ok else text
    except:
        return text

async def process_buffer(buffer, last_text_time):
    """Buffer işleme ve çeviri"""
    clean = clean_text(' '.join(buffer))
    if not clean or len(clean.split()) < MIN_WORDS:
        return last_text_time
    
    # Doğal zamanlama kontrolü
    current_time = time.time()
    if current_time - last_text_time < REALTIME_INTERVAL:
        return last_text_time
        
    lang = detect_language(clean)
    translated = await translate_to_turkish(clean)
    
    with print_lock:
        print(f"\n\033[1;34m[{lang.upper()}]\033[0m {clean}")
        print(f"\033[1;32m[TR]\033[0m {translated}\n")
    
    await broadcast(translated)
    audio_queue.put(translated)
    return current_time

async def audio_stream_processor(url):
    """Akıcı ses işleme döngüsü"""
    cmd = [
        'ffmpeg', '-i', url, '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-'
    ]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    
    text_buffer = []
    last_text_time = time.time()
    is_initial_buffer = True
    
    try:
        while True:
            data = proc.stdout.read(CHUNK_SIZE)
            if not data:
                await asyncio.sleep(0.01)
                continue
                
            if recognizer.AcceptWaveform(data):
                text = json.loads(recognizer.Result()).get('text', '').strip()
                if text:
                    text_buffer.append(text)
                    current_time = time.time()
                    
                    # İlk buffer dolunca veya normal aralıkta işle
                    if (is_initial_buffer and current_time - last_text_time > INITIAL_BUFFER_TIME) or \
                       (not is_initial_buffer and len(text_buffer) >= MAX_WORDS):
                        
                        last_text_time = await process_buffer(text_buffer, last_text_time)
                        text_buffer = []
                        is_initial_buffer = False
                        
            await asyncio.sleep(0.005)
    finally:
        proc.terminate()

async def broadcast(message):
    """Tüm istemcilere yayın"""
    if clients:
        await asyncio.wait([ws.send(message) for ws in clients], timeout=0.5)

async def client_handler(ws, path):
    """WebSocket bağlantı yönetimi"""
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
    
    print("\n\033[1;36mAKICI GERÇEK ZAMANLI ÇEVİRİ\033[0m")
    print(f"\033[90mStream: {url}\033[0m")
    print("\033[93mBaşlangıç buffer: 10s | Sonraki çeviriler: 3-4s aralıklarla\033[0m\n")
    
    try:
        await audio_stream_processor(url)
    except asyncio.CancelledError:
        logger.info("Servis durduruluyor...")
    except Exception as e:
        logger.error(f"Hata: {e}")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Akıcı Gerçek Zamanlı Çeviri')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Kritik hata: {e}")
