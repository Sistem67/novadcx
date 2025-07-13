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
logger = logging.getLogger('BağlamKoruyucu')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
TARGET_INTERVAL = 3.5  # Doğal konuşma aralığı
MIN_CONTEXT_WORDS = 5
MAX_CONTEXT_WORDS = 15
PAUSE_THRESHOLD = 1.2  # Cümle sonu bekleme süresi

# Model yolları
VOSK_MODEL_PATH = "/root/vosk-model-en-us-0.22"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Global nesneler
model = Model(VOSK_MODEL_PATH)
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
clients = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()
context_buffer = deque(maxlen=3)  # Son 3 cümleyi sakla

def play_audio(data):
    """Senkron ses çalma"""
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate)
            sd.wait()
    except Exception as e:
        logger.error(f"Ses oynatma hatası: {e}")

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

def is_sentence_complete(text):
    """Cümle tamamlık kontrolü"""
    return any(text.endswith(punct) for punct in ('.', '!', '?'))

async def enhance_translation(text, context):
    """Bağlam duyarlı çeviri"""
    # Önceki cümlelerle birlikte çevir
    full_context = ' '.join(context) + ' ' + text if context else text
    
    try:
        # LibreTranslate ile bağlam duyarlı çeviri
        params = {
            'q': full_context,
            'source': 'auto',
            'target': 'tr',
            'format': 'text'
        }
        response = await asyncio.to_thread(
            requests.post,
            "https://libretranslate.de/translate",
            data=params,
            timeout=2.5
        )
        if response.status_code == 200:
            translated = response.json().get('translatedText', '')
            # Sadece son cümlenin çevirisini al
            return translated.split('. ')[-1].strip()
    except Exception as e:
        logger.debug(f"Çeviri hatası: {e}")
    
    return text  # Fallback

async def process_stream(url):
    """Bağlam koruyan ses işleme"""
    cmd = [
        'ffmpeg', '-i', url, '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-'
    ]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    
    current_sentence = []
    last_update = time.time()
    
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
                    current_sentence.append(text)
                    last_update = time.time()
                    
                    # Cümle tamamlandı mı kontrolü
                    sentence_complete = is_sentence_complete(text)
                    elapsed_time = time.time() - last_update
                    
                    if (sentence_complete and len(current_sentence) >= MIN_CONTEXT_WORDS) or \
                       (elapsed_time >= PAUSE_THRESHOLD and len(current_sentence) >= MIN_CONTEXT_WORDS) or \
                       (len(current_sentence) >= MAX_CONTEXT_WORDS):
                        
                        full_text = ' '.join(current_sentence)
                        translated = await enhance_translation(full_text, list(context_buffer))
                        
                        with print_lock:
                            print(f"\n\033[1;34m[ORJINAL]\033[0m {full_text}")
                            print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
                        
                        await broadcast(translated)
                        audio_queue.put(translated)
                        context_buffer.append(full_text)
                        current_sentence = []
                        
            await asyncio.sleep(0.005)
    finally:
        proc.terminate()

async def broadcast(message):
    """İstemcilere yayın"""
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
    threading.Thread(target=tts_worker, daemon=True).start()
    
    server = await websockets.serve(
        client_handler,
        "0.0.0.0",
        8000,
        ping_interval=20,
        ping_timeout=10
    )
    
    print("\n\033[1;36mBAĞLAM KORUYUCU ÇEVİRİ AKTİF\033[0m")
    print(f"\033[90mKaynak: {url}\033[0m")
    print("\033[93mÖzellikler:\033[0m")
    print("- Cümle bütünlüğü koruma")
    print("- Bağlam duyarlı çeviri")
    print(f"- {TARGET_INTERVAL}s doğal aralıklar\n")
    
    try:
        await process_stream(url)
    except asyncio.CancelledError:
        logger.info("Durduruluyor...")
    except Exception as e:
        logger.error(f"Hata: {e}")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Bağlam Koruyucu Çeviri')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Başlatma hatası: {e}")
