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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('RTTranslation')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
MAX_BUFFER_LENGTH = 35
MIN_BUFFER_LENGTH = 4
PAUSE_THRESHOLD = 0.8
CONTEXT_WINDOW_SIZE = 2
LIBRETRANSLATE_URL = "https://libretranslate.de/translate"
GOOGLE_TRANSLATE_FALLBACK = False
LANGUAGE_DETECTION_THRESHOLD = 0.65
MAX_CACHE_SIZE = 500
TTL_CACHE_SECONDS = 3600 * 2

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
audio_queue = Queue(maxsize=10)
print_lock = threading.Lock()

# Dil desteği
SUPPORTED_LANGUAGES = {
    'en': 'English', 'tr': 'Turkish', 'de': 'German',
    'fr': 'French', 'es': 'Spanish', 'it': 'Italian',
    'ru': 'Russian', 'ar': 'Arabic', 'zh': 'Chinese',
    'ja': 'Japanese', 'ko': 'Korean'
}

TERM_DICTIONARY = {
    'en': {
        'hello': 'merhaba',
        'world': 'dünya',
        'computer': 'bilgisayar',
        'good': 'iyi',
        'morning': 'sabah'
    }
}

class TranslationCache:
    """Hafif bellek içi önbellek"""
    def __init__(self):
        self.cache = {}
        self.timestamps = {}
        
    def get(self, key):
        return self.cache.get(key) if time.time() - self.timestamps.get(key, 0) < TTL_CACHE_SECONDS else None
        
    def set(self, key, value):
        if len(self.cache) >= MAX_CACHE_SIZE:
            oldest_key = min(self.timestamps, key=self.timestamps.get)
            del self.cache[oldest_key]
            del self.timestamps[oldest_key]
        self.cache[key] = value
        self.timestamps[key] = time.time()

translation_cache = TranslationCache()

def play_audio(data):
    """Doğrudan bellekten ses çal"""
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            data = f.read(dtype='float32')
            sd.play(data, f.samplerate)
            sd.wait()
    except Exception as e:
        logger.warning(f"Ses oynatma hatası: {e}")

def tts_worker():
    """Temp dosyası kullanmayan TTS işçisi"""
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

@lru_cache(maxsize=MAX_CACHE_SIZE)
def clean_text(text):
    """Metin temizleme"""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)
    return text.capitalize()

def detect_language(text):
    """Hızlı dil tespiti"""
    try:
        if len(text.split()) < 2:
            return 'en'
        pred = fasttext_model.predict(text, k=1)
        return pred[0][0].replace('__label__', '') if pred[1][0] >= LANGUAGE_DETECTION_THRESHOLD else 'en'
    except:
        return 'en'

async def translate_text(text, src_lang):
    """Hızlı çeviri işlemi"""
    if src_lang in TERM_DICTIONARY:
        for term, trans in TERM_DICTIONARY[src_lang].items():
            if term.lower() in text.lower():
                text = text.replace(term, trans)
    
    try:
        params = {'q': text, 'source': src_lang, 'target': 'tr', 'format': 'text'}
        resp = await asyncio.to_thread(requests.post, LIBRETRANSLATE_URL, data=params, timeout=2)
        return resp.json().get('translatedText', text) if resp.ok else text
    except:
        return text

async def process_translation(buffer, lang):
    """Çeviri işlem hattı"""
    clean = clean_text(buffer)
    if not clean or len(clean.split()) < MIN_BUFFER_LENGTH:
        return None
        
    cache_key = f"{lang}_tr_{clean}"
    if cached := translation_cache.get(cache_key):
        return cached
        
    translated = await translate_text(clean, lang)
    if translated and translated != clean:
        translation_cache.set(cache_key, translated)
        with print_lock:
            print(f"\n\033[94m[ORJINAL]\033[0m: {clean}")
            print(f"\033[92m[ÇEVİRİ]\033[0m: {translated}\n")
        return translated
    return None

async def audio_stream_processor(url):
    """Ses işleme döngüsü"""
    cmd = ['ffmpeg', '-i', url, '-loglevel', 'quiet', '-ar', str(SAMPLE_RATE),
           '-ac', '1', '-f', 's16le', '-']
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    
    buffer = ""
    last_active = time.time()
    current_lang = 'en'
    
    try:
        while True:
            data = proc.stdout.read(CHUNK_SIZE)
            if not data:
                await asyncio.sleep(0.05)
                continue
                
            if recognizer.AcceptWaveform(data):
                text = json.loads(recognizer.Result()).get('text', '').strip()
                if text:
                    buffer = f"{buffer} {text}".strip()
                    last_active = time.time()
                    
                    if len(buffer.split()) >= MAX_BUFFER_LENGTH or (time.time() - last_active) > PAUSE_THRESHOLD:
                        if len(buffer.split()) > 2:
                            current_lang = detect_language(buffer)
                            
                        if translated := await process_translation(buffer, current_lang):
                            await broadcast(translated)
                            audio_queue.put(translated)
                        buffer = ""
                        
            await asyncio.sleep(0.01)
    finally:
        proc.terminate()

async def broadcast(message):
    """Tüm istemcilere yayın"""
    if clients:
        await asyncio.wait([ws.send(message) for ws in clients], timeout=1)

async def client_handler(ws, path):
    """WebSocket bağlantı yöneticisi"""
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
    
    print("\n\033[1;36mGERÇEK ZAMANLI ÇEVİRİ SERVİSİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m\n")
    
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
    parser = argparse.ArgumentParser(description='Gerçek Zamanlı Çeviri Servisi')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Kritik hata: {e}")
