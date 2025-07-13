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
os.nice(19)
os.environ['OMP_NUM_THREADS'] = '1'

# Logging konfigürasyonu
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('AutoTranslate')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
MAX_BUFFER_LENGTH = 10  # Daha kısa buffer
MIN_BUFFER_LENGTH = 3   # Daha küçük minimum uzunluk
PAUSE_THRESHOLD = 0.5   # Daha hızlı yanıt
FORCE_TRANSLATE = True  # Her durumda çeviri yap

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
audio_queue = Queue(maxsize=5)
print_lock = threading.Lock()

def play_audio(data):
    """Bellekten ses çal"""
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate)
            sd.wait()
    except Exception as e:
        logger.warning(f"Ses oynatma hatası: {e}")

def tts_worker():
    """Temp dosyasız TTS"""
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

@lru_cache(maxsize=1000)
def clean_text(text):
    """Metin temizleme"""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)
    return text.capitalize()

def detect_language(text):
    """Güçlü dil tespiti"""
    try:
        if len(text.split()) < 2:
            return 'en'
        pred = fasttext_model.predict(text, k=1)
        return pred[0][0].replace('__label__', '') if pred[1][0] >= 0.5 else 'en'
    except:
        return 'en'

async def translate_to_turkish(text, src_lang='auto'):
    """Kesin Türkçe çeviri"""
    try:
        params = {
            'q': text,
            'source': src_lang,
            'target': 'tr',
            'format': 'text'
        }
        resp = await asyncio.to_thread(
            requests.post,
            "https://libretranslate.de/translate",
            data=params,
            timeout=2
        )
        return resp.json().get('translatedText', text) if resp.ok else text
    except:
        return text

async def process_audio_stream(url):
    """Ses işleme ve çeviri döngüsü"""
    cmd = [
        'ffmpeg', '-i', url, '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-'
    ]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    
    buffer = ""
    last_update = time.time()
    
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
                    last_update = time.time()
                    
                    # Daha sık işleme tetikleme
                    if (len(buffer.split()) >= MAX_BUFFER_LENGTH or 
                        (time.time() - last_update) > PAUSE_THRESHOLD):
                        
                        clean = clean_text(buffer)
                        if clean:
                            lang = detect_language(clean)
                            translated = await translate_to_turkish(clean, lang)
                            
                            with print_lock:
                                print(f"\n\033[1;34m[ORJINAL]\033[0m {lang.upper()}: {clean}")
                                print(f"\033[1;32m[ÇEVİRİ]\033[0m TR: {translated}\n")
                            
                            await broadcast(translated)
                            audio_queue.put(translated)
                            
                        buffer = ""
                        
            await asyncio.sleep(0.01)
    finally:
        proc.terminate()

async def broadcast(message):
    """Tüm istemcilere gönder"""
    if clients:
        await asyncio.wait([ws.send(message) for ws in clients], timeout=1)

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
        ping_interval=15,
        ping_timeout=10
    )
    
    print("\n\033[1;36mOTOMATİK TÜRKÇE ÇEVİRİ SERVİSİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print("\033[93mÇıkmak için Ctrl+C\033[0m\n")
    
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
    parser = argparse.ArgumentParser(description='Otomatik Türkçe Çeviri Servisi')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Kritik hata: {e}")
