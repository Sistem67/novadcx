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
import numpy as np
import requests
import websockets
from gtts import gTTS
from vosk import KaldiRecognizer, Model

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('translation_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('TranslationService')

# Constants
SAMPLE_RATE = 16000
CHUNK_SIZE = 8192  # Optimized for audio processing
MAX_BUFFER_LENGTH = 60  # Optimal for translation context
MIN_BUFFER_LENGTH = 10  # Minimum words to process
PAUSE_THRESHOLD = 1.5  # Seconds of silence to trigger processing
CONTEXT_WINDOW_SIZE = 3  # Context sentences to keep
LIBRETRANSLATE_URL = "https://libretranslate.com/translate"
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
LANGUAGE_DETECTION_THRESHOLD = 0.8
MAX_CACHE_SIZE = 2000
TTL_CACHE_SECONDS = 3600 * 6  # 6 hours cache

# Model paths
VOSK_MODEL_PATH = "/root/vosk-model-en-us-0.22"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Global objects
try:
    model = Model(VOSK_MODEL_PATH)
    fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
except Exception as e:
    logger.error(f"Model loading failed: {e}")
    raise

clients = set()
tts_queue = Queue()

# Language and translation configuration
LANGUAGE_MAP = {
    'en': 'English', 'tr': 'Turkish', 'de': 'German', 'fr': 'French',
    'es': 'Spanish', 'it': 'Italian', 'ru': 'Russian', 'ar': 'Arabic'
}

TERM_DICTIONARY = {
    'en': {
        'machine learning': 'makine öğrenmesi',
        'neural network': 'yapay sinir ağı',
        'artificial intelligence': 'yapay zeka',
    },
    'tr': {
        'yapay zeka': 'artificial intelligence',
        'makine öğrenmesi': 'machine learning'
    }
}

class TranslationCache:
    """LRU cache with TTL for translations"""
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
    """Background worker for text-to-speech conversion"""
    while True:
        text = tts_queue.get()
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as fp:
                tts = gTTS(text=text, lang='tr', lang_check=False)
                tts.save(fp.name)
                subprocess.run(
                    ["mpg123", "-q", "--gain", "3", "--mono", fp.name],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    check=True
                )
        except subprocess.CalledProcessError as e:
            logger.warning(f"TTS playback error: {e}")
        except Exception as e:
            logger.error(f"TTS generation error: {e}")
        finally:
            tts_queue.task_done()

@lru_cache(maxsize=MAX_CACHE_SIZE)
def clean_text(text):
    """Clean and normalize text for processing"""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)
    sentences = re.split(r'([.!?] )', text)
    return ''.join([s[0].upper() + s[1:] if s else '' for s in sentences])

def detect_language(text):
    """Detect language with confidence threshold"""
    try:
        predictions = fasttext_model.predict(text.replace("\n", " "), k=1)
        if predictions[1][0] >= LANGUAGE_DETECTION_THRESHOLD:
            return predictions[0][0].replace('__label__', '')
    except Exception as e:
        logger.warning(f"Language detection failed: {e}")
    return 'en'  # Default fallback

def should_process(buffer, last_activity_time):
    """Determine if buffer should be processed"""
    word_count = len(buffer.split())
    time_since_last = time.time() - last_activity_time
    
    return (word_count >= MAX_BUFFER_LENGTH or 
            (word_count >= MIN_BUFFER_LENGTH and time_since_last >= PAUSE_THRESHOLD))

async def translate_text(text, src_lang):
    """Translate text using available services with fallback"""
    # Check term dictionary first
    if src_lang in TERM_DICTIONARY:
        for term, translation in TERM_DICTIONARY[src_lang].items():
            if term.lower() in text.lower():
                text = re.sub(re.escape(term), translation, text, flags=re.IGNORECASE)

    # Try LibreTranslate
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
            timeout=5,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        if response.status_code == 200:
            result = response.json()
            return result.get('translatedText', text)
    except Exception as e:
        logger.debug(f"LibreTranslate failed: {e}")

    # Fallback to Google Translate
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
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list) and data[0]:
                return ''.join([x[0] for x in data[0] if x[0]])
    except Exception as e:
        logger.debug(f"Google Translate failed: {e}")

    return text  # Return original if all fails

async def handle_translation(buffer, context_buffer, detected_lang):
    """Handle the full translation pipeline"""
    try:
        clean_input = clean_text(buffer)
        if not clean_input or len(clean_input.split()) < MIN_BUFFER_LENGTH:
            return ""

        # Cache check
        cache_key = f"{detected_lang}_tr_{clean_input}"
        cached = translation_cache.get(cache_key)
        if cached:
            return cached

        # Actual translation
        translated = await translate_text(clean_input, detected_lang)
        if translated and translated != clean_input:
            translation_cache.set(cache_key, translated)
            return translated

    except Exception as e:
        logger.error(f"Translation pipeline error: {e}")
    return ""

async def recognize_and_translate(url):
    """Main audio processing and translation loop"""
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
        audio_stream = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        ).stdout
    except Exception as e:
        logger.error(f"FFmpeg process failed: {e}")
        raise

    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    
    buffer = ""
    context_buffer = deque(maxlen=CONTEXT_WINDOW_SIZE)
    last_activity = time.time()
    current_lang = 'en'
    
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
                    
                    # Update language detection periodically
                    if len(buffer.split()) % 10 == 0:
                        current_lang = detect_language(buffer)
                    
                    if should_process(buffer, last_activity):
                        translated = await handle_translation(buffer, context_buffer, current_lang)
                        if translated:
                            await send_to_clients(translated)
                            tts_queue.put(translated)
                            context_buffer.append(translated)
                            buffer = ""
                            
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.error(f"Recognition loop error: {e}")
        raise
    finally:
        audio_stream.close()

async def send_to_clients(message):
    """Send message to all connected WebSocket clients"""
    if not clients:
        return
        
    disconnected = set()
    for ws in clients:
        try:
            await ws.send(message)
        except Exception as e:
            logger.debug(f"Client send error: {e}")
            disconnected.add(ws)
    
    for ws in disconnected:
        clients.remove(ws)

async def client_handler(websocket, path):
    """Handle WebSocket client connections"""
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def main(url):
    """Main application entry point"""
    # Start TTS worker thread
    threading.Thread(target=tts_worker, daemon=True).start()
    
    # Start WebSocket server
    server = await websockets.serve(
        client_handler,
        "0.0.0.0",
        8000,
        ping_interval=30,
        ping_timeout=10
    )
    
    logger.info(f"Translation service started on port 8000, processing stream: {url}")
    
    try:
        await recognize_and_translate(url)
    except asyncio.CancelledError:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Real-time audio translation service')
    parser.add_argument('--url', required=True, help='Audio stream URL to process')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        logger.info("Service stopped by user")
    except Exception as e:
        logger.error(f"Service crashed: {e}")
        raise
