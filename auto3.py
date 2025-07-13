import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
import fasttext
import os
from gtts import gTTS
import tempfile
from queue import Queue
import threading
import re
from collections import deque
import logging
import time
import requests
import numpy as np
from functools import lru_cache

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('optimized_translation.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('TranslationService')

# Ayarlar
SAMPLE_RATE = 16000
CHUNK_SIZE = 8000  # Artırıldı
MAX_BUFFER_LENGTH = 80  # Artırıldı
MIN_BUFFER_LENGTH = 15  # Azaltıldı
PAUSE_THRESHOLD = 2.0  # Artırıldı
CONTEXT_WINDOW_SIZE = 3  # Azaltıldı
LIBRETRANSLATE_URL = "https://libretranslate.com/translate"
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
LANGUAGE_DETECTION_THRESHOLD = 0.75  # Düşürüldü

# Model yolları
VOSK_MODEL_PATH = "/root/vosk-model-en-us-0.22-lgraph"  # Daha hızlı model
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Önbellek boyutları
MAX_CACHE_SIZE = 1000
TTL_CACHE_SECONDS = 3600

class TranslationCache:
    def __init__(self):
        self.cache = {}
        self.timestamps = {}
        
    def get(self, key):
        if key in self.cache and (time.time() - self.timestamps[key]) < TTL_CACHE_SECONDS:
            return self.cache[key]
        return None
        
    def set(self, key, value):
        if len(self.cache) >= MAX_CACHE_SIZE:
            oldest_key = min(self.timestamps, key=self.timestamps.get)
            del self.cache[oldest_key]
            del self.timestamps[oldest_key]
        self.cache[key] = value
        self.timestamps[key] = time.time()

# Global nesneler
model = Model(VOSK_MODEL_PATH)
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
clients = set()
tts_queue = Queue()
translation_cache = TranslationCache()

# Dil eşleme ve terim sözlüğü (daha optimize)
LANGUAGE_MAP = {
    'en': 'English', 'tr': 'Turkish', 'de': 'German', 'fr': 'French',
    'es': 'Spanish', 'it': 'Italian', 'ru': 'Russian'
}

TERM_DICTIONARY = {
    'en': {
        'machine learning': 'makine öğrenmesi',
        'neural network': 'yapay sinir ağı',
        # ... diğer terimler
    }
}

@lru_cache(maxsize=MAX_CACHE_SIZE)
def clean_text(text):
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)
    sentences = re.split(r'([.!?] )', text)
    return ''.join([s.capitalize() for s in sentences if s]).strip()

def tts_worker():
    while True:
        text = tts_queue.get()
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3") as fp:
                gTTS(text=text, lang='tr').save(fp.name)
                subprocess.run(["mpg123", "-q", "--gain", "3", "--mono", fp.name], 
                              stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.error(f"TTS error: {str(e)}")
        tts_queue.task_done()

threading.Thread(target=tts_worker, daemon=True).start()

async def handle_translation(buffer, context_buffer, current_lang):
    try:
        clean_input = clean_text(buffer)
        if not clean_input:
            return ""
            
        # Önbellek kontrolü
        cache_key = f"{current_lang}_tr_{clean_input}"
        cached = translation_cache.get(cache_key)
        if cached:
            return cached
            
        # Çeviri işlemi
        translated = await translate_text(clean_input, current_lang)
        if translated:
            translation_cache.set(cache_key, translated)
            return translated
    except Exception as e:
        logger.error(f"Translation error: {str(e)}")
    return ""

async def translate_text(text, src_lang):
    # LibreTranslate denemesi
    try:
        params = {'q': text, 'source': src_lang, 'target': 'tr'}
        response = await asyncio.to_thread(
            requests.post, LIBRETRANSLATE_URL, data=params, timeout=3
        )
        if response.ok:
            return response.json().get('translatedText')
    except:
        pass
        
    # Google Translate fallback
    try:
        params = {
            'client': 'gtx',
            'sl': src_lang,
            'tl': 'tr',
            'dt': 't',
            'q': text
        }
        response = await asyncio.to_thread(
            requests.get, GOOGLE_TRANSLATE_URL, params=params, timeout=3
        )
        if response.ok:
            return ''.join([x[0] for x in response.json()[0] if x[0]])
    except:
        pass
        
    return text

async def recognize_and_translate(url):
    audio_stream = subprocess.Popen([
        'ffmpeg', '-i', url, '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-'
    ], stdout=subprocess.PIPE).stdout

    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    
    buffer = ""
    context_buffer = deque(maxlen=CONTEXT_WINDOW_SIZE)
    last_activity = time.time()
    
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
                
                if should_process(buffer, last_activity):
                    translated = await handle_translation(buffer, context_buffer, 'en')
                    if translated:
                        await send_to_clients(translated)
                        buffer = ""
                        
        await asyncio.sleep(0.05)

async def send_to_clients(text):
    if clients:
        await asyncio.gather(*[client.send(text) for client in clients], 
                           return_exceptions=True)

async def main(url):
    server = await websockets.serve(
        lambda ws, path: clients.add(ws) or ws.wait_closed() or clients.remove(ws),
        "0.0.0.0", 8000
    )
    logger.info("Service started on port 8000")
    await recognize_and_translate(url)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
