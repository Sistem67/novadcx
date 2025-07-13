import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
from googletrans import Translator
import fasttext
import os
from gtts import gTTS
import tempfile
from queue import Queue
import threading
import re
import time
from collections import defaultdict
import logging
import aiohttp
from concurrent.futures import ThreadPoolExecutor
import hashlib

# ----------------------
# KONFİGÜRASYON
# ----------------------
SAMPLE_RATE = 16000
CHUNK_SIZE = 8000  # Artırılmış buffer boyutu
VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"
MAX_CACHE_SIZE = 1000  # Çeviri önbellek boyutu
MIN_TRANSLATE_LENGTH = 3  # Çeviri yapılacak minimum kelime sayısı
MAX_TRANSLATE_LENGTH = 25  # Maksimum kelime sayısı
TTS_WORKERS = 2  # Paralel ses üretimi

# ----------------------
# LOGGING
# ----------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('translation_service_optimized.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ----------------------
# ÖNBELLEK MEKANİZMALARI
# ----------------------
translation_cache = {}
tts_cache = {}
request_timestamps = defaultdict(list)

# ----------------------
# MODEL YÜKLENMELERİ
# ----------------------
logger.info("Modeller yükleniyor...")
model = Model(VOSK_MODEL_PATH)
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
translator = Translator()
logger.info("Modeller başarıyla yüklendi")

# ----------------------
# İŞ PARÇACIKLARI VE KUYRUKLAR
# ----------------------
clients = set()
tts_queue = Queue()
audio_queue = Queue()
last_processed_time = time.time()

# ----------------------
# OPTİMİZE FONKSİYONLAR
# ----------------------
def rate_limiter(max_requests=5, per_seconds=1):
    """API isteklerini limitler"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            current_time = time.time()
            key = func.__name__
            
            # Eski istekleri temizle
            request_timestamps[key] = [
                t for t in request_timestamps[key] 
                if current_time - t < per_seconds
            ]
            
            if len(request_timestamps[key]) >= max_requests:
                await asyncio.sleep(per_seconds - (current_time - request_timestamps[key][0]))
            
            request_timestamps[key].append(time.time())
            return await func(*args, **kwargs)
        return wrapper
    return decorator

def clean_text(text):
    """Metni optimize şekilde temizler"""
    if not text:
        return ""
    
    # Hızlı temel temizlik
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)
    
    # Büyük/küçük harf normalleştirme
    if len(text) > 1:
        text = text[0].upper() + text[1:]
    
    return text.strip()

def generate_cache_key(text, target_lang='tr'):
    """Önbellek anahtarı oluşturur"""
    return hashlib.md5(f"{text}_{target_lang}".encode()).hexdigest()

# ----------------------
# ÇEKİRDEK İŞLEMLER
# ----------------------
async def translate_text(text, src_lang='auto', dest_lang='tr'):
    """Optimize edilmiş çeviri fonksiyonu"""
    if not text or len(text.split()) < MIN_TRANSLATE_LENGTH:
        return ""
    
    # Önbellek kontrolü
    cache_key = generate_cache_key(text, dest_lang)
    if cache_key in translation_cache:
        return translation_cache[cache_key]
    
    try:
        # Dil algılama (gerekmiyorsa atla)
        if src_lang == 'auto':
            lang_pred = fasttext_model.predict(text, k=1)
            src_lang = lang_pred[0][0].replace("__label__", "")
        
        # Çeviri işlemi
        translated = translator.translate(
            text,
            src=src_lang,
            dest=dest_lang
        ).text
        
        # Önbelleğe al
        if len(translation_cache) > MAX_CACHE_SIZE:
            translation_cache.pop(next(iter(translation_cache)))
        
        translation_cache[cache_key] = translated
        return translated
    
    except Exception as e:
        logger.error(f"Çeviri hatası: {e}")
        return ""

def text_to_speech(text, lang='tr'):
    """Optimize TTS fonksiyonu"""
    if not text:
        return
    
    cache_key = generate_cache_key(text, f"tts_{lang}")
    if cache_key in tts_cache:
        return tts_cache[cache_key]
    
    try:
        with tempfile.NamedTemporaryFile(delete=True, suffix=".mp3") as fp:
            tts = gTTS(text=text, lang=lang, slow=False)
            tts.save(fp.name)
            
            # Daha doğal ses için optimize parametreler
            subprocess.run(
                ["mpg123", "-q", "--gain", "3", "--mono", fp.name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Önbelleğe al
            if len(tts_cache) > MAX_CACHE_SIZE:
                tts_cache.pop(next(iter(tts_cache)))
            
            tts_cache[cache_key] = True
    except Exception as e:
        logger.error(f"TTS hatası: {e}")

# ----------------------
# İŞ PARÇACIĞI FONKSİYONLARI
# ----------------------
def audio_processing_worker():
    """Ses işleme işçisi"""
    while True:
        data = audio_queue.get()
        if data:
            try:
                recognizer = KaldiRecognizer(model, SAMPLE_RATE)
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    text = result.get('text', '').strip()
                    if text:
                        process_text(text)
            except Exception as e:
                logger.error(f"Ses işleme hatası: {e}")
        audio_queue.task_done()

def tts_worker():
    """TTS işçisi"""
    while True:
        text = tts_queue.get()
        if text:
            text_to_speech(text)
        tts_queue.task_done()

# ----------------------
# ASENKRON İŞLEMLER
# ----------------------
async def process_text(text):
    """Metin işleme pipeline'ı"""
    global last_processed_time
    
    cleaned_text = clean_text(text)
    if not cleaned_text:
        return
    
    last_processed_time = time.time()
    
    # Paralel çeviri ve TTS
    translated = await translate_text(cleaned_text)
    if translated:
        await send_to_clients(translated)
        tts_queue.put(translated)

async def send_to_clients(text):
    """WebSocket istemcilerine veri gönderir"""
    if clients:
        try:
            await asyncio.gather(
                *[client.send(text) for client in clients],
                return_exceptions=True
            )
        except Exception as e:
            logger.error(f"İstemci gönderim hatası: {e}")

async def audio_stream_processor(url):
    """Ses akışını işler"""
    ffmpeg_process = subprocess.Popen([
        'ffmpeg',
        '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le',
        '-'
    ], stdout=subprocess.PIPE)
    
    while True:
        data = ffmpeg_process.stdout.read(CHUNK_SIZE)
        if not data:
            break
        audio_queue.put(data)

# ----------------------
# WEB SOKET SERVER
# ----------------------
async def websocket_handler(websocket, path):
    clients.add(websocket)
    try:
        async for message in websocket:
            pass  # İstemciden gelen mesajları dinle
    finally:
        clients.remove(websocket)

# ----------------------
# ANA UYGULAMA
# ----------------------
async def main(url):
    # İş parçacıklarını başlat
    threading.Thread(target=audio_processing_worker, daemon=True).start()
    for _ in range(TTS_WORKERS):
        threading.Thread(target=tts_worker, daemon=True).start()
    
    # WebSocket sunucusu
    async with websockets.serve(
        websocket_handler, 
        "0.0.0.0", 
        8000,
        ping_interval=20,
        ping_timeout=30
    ):
        logger.info("WebSocket servisi başlatıldı")
        await audio_stream_processor(url)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Optimize Çeviri Sistemi")
    parser.add_argument('--url', required=True, help='Ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        logger.info("Servis kapatılıyor")
    except Exception as e:
        logger.error(f"Kritik hata: {e}")
