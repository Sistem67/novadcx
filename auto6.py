#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import re
import subprocess
import tempfile
import threading
import time
from collections import deque
from functools import lru_cache
from queue import Queue

import fasttext
import requests
import websockets
from gtts import gTTS
from vosk import KaldiRecognizer, Model

# Logging konfigürasyonu
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('translation_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('TranslationService')

# Sabitler
SAMPLE_RATE = 16000
CHUNK_SIZE = 8192  # Ses işleme için optimize edilmiş
MAX_BUFFER_LENGTH = 50  # Çeviri için ideal uzunluk
MIN_BUFFER_LENGTH = 8   # İşlem için minimum kelime sayısı
PAUSE_THRESHOLD = 1.2   # İşlem tetikleme için sessizlik süresi (saniye)
CONTEXT_WINDOW_SIZE = 2  # Bağlam için tutulan cümle sayısı
LIBRETRANSLATE_URL = "https://libretranslate.com/translate"
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
LANGUAGE_DETECTION_THRESHOLD = 0.85
MAX_CACHE_SIZE = 3000
TTL_CACHE_SECONDS = 3600 * 8  # 8 saat önbellek

# Model yolları
VOSK_MODEL_PATH = "/root/vosk-model-en-us-0.22"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Global nesneler
try:
    model = Model(VOSK_MODEL_PATH)
    fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
except Exception as e:
    logger.error(f"Model yükleme hatası: {e}")
    raise

clients = set()
tts_queue = Queue()
audio_processes = set()

# Dil ve çeviri konfigürasyonu
LANGUAGE_MAP = {
    'en': 'English', 'tr': 'Turkish', 'de': 'German', 'fr': 'French',
    'es': 'Spanish', 'it': 'Italian', 'ru': 'Russian', 'ar': 'Arabic'
}

TERM_DICTIONARY = {
    'en': {
        'machine learning': 'makine öğrenmesi',
        'neural network': 'yapay sinir ağı',
        'artificial intelligence': 'yapay zeka',
        'deep learning': 'derin öğrenme',
        'computer vision': 'bilgisayarlı görü'
    },
    'tr': {
        'yapay zeka': 'artificial intelligence',
        'makine öğrenmesi': 'machine learning',
        'derin öğrenme': 'deep learning',
        'bilgisayarlı görü': 'computer vision'
    }
}

class TranslationCache:
    """TTL ile LRU önbellek"""
    def __init__(self):
        self.cache = {}
        self.timestamps = {}
        self.lock = threading.Lock()
        
    def get(self, key):
        with self.lock:
            if key in self.cache and (time.time() - self.timestamps[key]) < TTL_CACHE_SECONDS:
                return self.cache[key]
        return None
        
    def set(self, key, value):
        with self.lock:
            if len(self.cache) >= MAX_CACHE_SIZE:
                oldest_key = min(self.timestamps, key=self.timestamps.get)
                del self.cache[oldest_key]
                del self.timestamps[oldest_key]
            self.cache[key] = value
            self.timestamps[key] = time.time()

translation_cache = TranslationCache()

def tts_worker():
    """Metinden sese dönüşüm için arka plan işçisi"""
    while True:
        text = tts_queue.get()
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as fp:
                tts = gTTS(text=text, lang='tr', lang_check=False, slow=False)
                tts.save(fp.name)
                proc = subprocess.run(
                    ["mpg123", "-q", "--gain", "2.5", "--mono", fp.name],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    check=True
                )
        except subprocess.CalledProcessError as e:
            logger.warning(f"Ses oynatma hatası: {e}")
        except Exception as e:
            logger.error(f"Ses oluşturma hatası: {e}")
        finally:
            tts_queue.task_done()

@lru_cache(maxsize=MAX_CACHE_SIZE)
def clean_text(text):
    """İşleme için metni temizle ve normalleştir"""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return ' '.join([s[0].upper() + s[1:] for s in sentences if s])

def detect_language(text):
    """Güven eşiği ile dil tespiti"""
    try:
        predictions = fasttext_model.predict(text.replace("\n", " "), k=1)
        if predictions[1][0] >= LANGUAGE_DETECTION_THRESHOLD:
            lang = predictions[0][0].replace('__label__', '')
            logger.debug(f"Tespit edilen dil: {LANGUAGE_MAP.get(lang, lang)}")
            return lang
    except Exception as e:
        logger.warning(f"Dil tespit hatası: {e}")
    return 'en'  # Varsayılan dil

def should_process(buffer, last_activity_time):
    """Buffer'ın işlenip işlenmeyeceğine karar ver"""
    words = buffer.split()
    word_count = len(words)
    time_since_last = time.time() - last_activity_time
    
    # Cümle sınırlarını kontrol et
    sentence_end = any(buffer.endswith(punct) for punct in ('.', '!', '?'))
    
    return (word_count >= MAX_BUFFER_LENGTH or 
            (word_count >= MIN_BUFFER_LENGTH and 
             (time_since_last >= PAUSE_THRESHOLD or sentence_end)))

async def translate_text(text, src_lang):
    """Önce terim sözlüğüne, sonra çeviri servislerine bak"""
    original_text = text
    
    # Terim sözlüğü kontrolü
    if src_lang in TERM_DICTIONARY:
        for term, translation in TERM_DICTIONARY[src_lang].items():
            if re.search(rf'\b{re.escape(term)}\b', text, flags=re.IGNORECASE):
                text = re.sub(rf'\b{re.escape(term)}\b', translation, text, flags=re.IGNORECASE)
    
    if text != original_text:
        logger.debug(f"Terim sözlüğünden çevrildi: {original_text} -> {text}")
        return text

    # LibreTranslate denemesi
    try:
        params = {
            'q': text,
            'source': src_lang,
            'target': 'tr',
            'format': 'text'
        }
        response = await asyncio.to_thread(
            requests.post,
            LIBRETRANSLATE_URL,
            data=params,
            timeout=4,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        if response.status_code == 200:
            result = response.json()
            translated = result.get('translatedText', text)
            if translated != text:
                logger.debug(f"LibreTranslate çevirisi: {text[:50]}... -> {translated[:50]}...")
                return translated
    except Exception as e:
        logger.debug(f"LibreTranslate hatası: {e}")

    # Google Translate yedeklemesi
    try:
        params = {
            'client': 'gtx',
            'sl': src_lang,
            'tl': 'tr',
            'dt': 't',
            'q': text
        }
        response = await asyncio.to_thread(
            requests.get,
            GOOGLE_TRANSLATE_URL,
            params=params,
            timeout=4
        )
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list) and data[0]:
                translated = ''.join([x[0] for x in data[0] if x[0]])
                if translated != text:
                    logger.debug(f"Google Translate çevirisi: {text[:50]}... -> {translated[:50]}...")
                    return translated
    except Exception as e:
        logger.debug(f"Google Translate hatası: {e}")

    return original_text

async def handle_translation(buffer, context_buffer, detected_lang):
    """Tam çeviri işlem hattı"""
    try:
        clean_input = clean_text(buffer)
        if not clean_input or len(clean_input.split()) < MIN_BUFFER_LENGTH:
            return ""

        # Önbellek kontrolü
        cache_key = f"{detected_lang}_tr_{clean_input}"
        cached = translation_cache.get(cache_key)
        if cached:
            logger.debug(f"Önbellekten çeviri: {clean_input[:50]}...")
            return cached

        # Çeviri işlemi
        translated = await translate_text(clean_input, detected_lang)
        if translated and translated != clean_input:
            translation_cache.set(cache_key, translated)
            return translated

    except Exception as e:
        logger.error(f"Çeviri hatası: {e}")
    return ""

async def recognize_and_translate(url):
    """Ana ses işleme ve çeviri döngüsü"""
    ffmpeg_cmd = [
        'ffmpeg',
        '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le',
        '-'
    ]
    
    try:
        audio_process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=CHUNK_SIZE
        )
        audio_processes.add(audio_process)
        audio_stream = audio_process.stdout
    except Exception as e:
        logger.error(f"FFmpeg başlatma hatası: {e}")
        raise

    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    
    buffer = ""
    context_buffer = deque(maxlen=CONTEXT_WINDOW_SIZE)
    last_activity = time.time()
    current_lang = 'en'
    consecutive_repeats = 0
    last_translation = ""
    
    try:
        while True:
            data = audio_stream.read(CHUNK_SIZE)
            if not data:
                await asyncio.sleep(0.1)
                continue
                
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get('text', '').strip()
                
                if text:
                    buffer = f"{buffer} {text}" if buffer else text
                    last_activity = time.time()
                    
                    # Periyodik dil güncelleme
                    if len(buffer.split()) % 15 == 0:
                        current_lang = detect_language(buffer)
                    
                    if should_process(buffer, last_activity):
                        translated = await handle_translation(buffer, context_buffer, current_lang)
                        if translated:
                            # Tekrarlanan çevirileri filtrele
                            if translated == last_translation:
                                consecutive_repeats += 1
                                if consecutive_repeats > 1:
                                    logger.debug("Tekrarlanan çeviri filtrelendi")
                                    buffer = ""
                                    continue
                            else:
                                consecutive_repeats = 0
                            
                            await send_to_clients(translated)
                            tts_queue.put(translated)
                            context_buffer.append(translated)
                            last_translation = translated
                            buffer = ""
                            
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.error(f"Tanıma döngüsü hatası: {e}")
        raise
    finally:
        audio_process.terminate()
        audio_processes.discard(audio_process)

async def send_to_clients(message):
    """Tüm bağlı istemcilere mesaj gönder"""
    if not clients:
        return
        
    disconnected = set()
    for ws in clients:
        try:
            await ws.send(message)
        except Exception as e:
            logger.debug(f"İstemci gönderme hatası: {e}")
            disconnected.add(ws)
    
    for ws in disconnected:
        clients.remove(ws)

async def client_handler(websocket, path):
    """WebSocket istemci bağlantılarını yönet"""
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def cleanup():
    """Kaynakları temizle"""
    for proc in audio_processes:
        proc.terminate()
    audio_processes.clear()

async def main(url):
    """Ana uygulama giriş noktası"""
    # TTS işçi thread'i başlat
    threading.Thread(target=tts_worker, daemon=True).start()
    
    # WebSocket sunucusu başlat
    server = await websockets.serve(
        client_handler,
        "0.0.0.0",
        8000,
        ping_interval=20,
        ping_timeout=10,
        max_size=2**20  # 1MB maksimum mesaj boyutu
    )
    
    logger.info(f"Çeviri servisi başlatıldı. Port: 8000, Stream: {url}")
    
    try:
        await recognize_and_translate(url)
    except asyncio.CancelledError:
        logger.info("Servis kapatılıyor...")
    except Exception as e:
        logger.error(f"Kritik hata: {e}")
        raise
    finally:
        await cleanup()
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Gerçek zamanlı ses çeviri servisi')
    parser.add_argument('--url', required=True, help='İşlenecek ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        logger.info("Kullanıcı tarafından durduruldu")
    except Exception as e:
        logger.error(f"Servis çöktü: {e}")
        raise
