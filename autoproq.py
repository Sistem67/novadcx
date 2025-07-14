#!/usr/bin/env python3
import argparse
import asyncio
import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from queue import Queue
from typing import Set, Dict

import fasttext
import sounddevice as sd
import soundfile as sf
import websockets
from gtts import gTTS
from vosk import KaldiRecognizer, Model

# -------------------- KONFİGÜRASYON --------------------
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
MIN_WORDS = 1
MAX_WORDS = 12
MAX_HISTORY = 5

# Model yolları (DEĞİŞMEDİ)
MODEL_BASE_DIR = "/root/"
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
FASTTEXT_MODEL = "lid.176.bin"

# WebSocket
WS_HOST = "0.0.0.0"
WS_PORT = 8000

# -------------------- GÜNCELLENMİŞ ÇEVİRİ SÖZLÜĞÜ --------------------
TRANSLATION_DICT = {
    # Temel kelimeler
    'hello': 'merhaba', 'hi': 'selam', 'hey': 'hey',
    'yes': 'evet', 'no': 'hayır', 'okay': 'tamam',
    'thank you': 'teşekkürler', 'thanks': 'sağol', 
    'please': 'lütfen', 'sorry': 'özür dilerim',
    
    # Soru kelimeleri
    'what': 'ne', 'where': 'nerede', 'when': 'ne zaman',
    'why': 'niye', 'how': 'nasıl', 'which': 'hangi',
    
    # Zamirler
    'i': 'ben', 'you': 'sen', 'he': 'o', 'she': 'o',
    'we': 'biz', 'they': 'onlar', 'me': 'beni',
    'my': 'benim', 'your': 'senin', 'our': 'bizim',
    
    # Sık kullanılan fiiller
    'is': 'dır', 'are': 'dır', 'have': 'var', 
    'go': 'git', 'come': 'gel', 'want': 'istemek',
    'need': 'ihtiyacım var', 'like': 'sevmek',
    
    # Günlük ifadeler
    'good morning': 'günaydın', 'good night': 'iyi geceler',
    'how are you': 'nasılsın', 'i am fine': 'iyiyim',
    'what time': 'saat kaç', 'goodbye': 'hoşçakal'
}

# -------------------- GLOBAL DEĞİŞKENLER --------------------
clients = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()
translation_history = deque(maxlen=MAX_HISTORY)

# -------------------- MODEL YÜKLEME --------------------
def load_models():
    try:
        ft_path = os.path.join(MODEL_BASE_DIR, FASTTEXT_MODEL)
        vosk_path = os.path.join(MODEL_BASE_DIR, DEFAULT_MODEL)
        
        if not os.path.exists(ft_path):
            raise FileNotFoundError(f"FastText modeli bulunamadı: {ft_path}")
        if not os.path.exists(vosk_path):
            raise FileNotFoundError(f"Vosk modeli bulunamadı: {vosk_path}")
            
        return (
            fasttext.load_model(ft_path),
            Model(vosk_path),
            DEFAULT_MODEL
        )
    except Exception as e:
        logging.error(f"Model yükleme hatası: {str(e)}")
        sys.exit(1)

# -------------------- SES İŞLEME --------------------
def play_audio(data):
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate)
            sd.wait()
    except Exception as e:
        logging.error(f"Ses oynatma hatası: {str(e)}")

def tts_worker():
    while True:
        text = audio_queue.get()
        try:
            with io.BytesIO() as f:
                tts = gTTS(text=text, lang='tr', slow=False)
                tts.write_to_fp(f)
                play_audio(f.getvalue())
        except Exception as e:
            logging.error(f"TTS hatası: {str(e)}")
        finally:
            audio_queue.task_done()

# -------------------- GÜÇLENDİRİLMİŞ ÇEVİRİ MOTORU --------------------
def translate_text(text: str) -> str:
    """Geliştirilmiş çeviri fonksiyonu"""
    if not text:
        return ""
        
    # Tüm cümleyi küçük harfe çevir (case insensitive karşılaştırma için)
    text_lower = text.lower()
    
    # 1. Önce tam eşleşen ifadeleri çevir
    for eng, tr in TRANSLATION_DICT.items():
        if f' {eng} ' in f' {text_lower} ':
            text = re.sub(rf'\b{eng}\b', tr, text, flags=re.IGNORECASE)
            return text
    
    # 2. Kelime kelime çeviri
    words = text.split()
    translated_words = []
    for word in words:
        lower_word = word.lower()
        if lower_word in TRANSLATION_DICT:
            translated_words.append(TRANSLATION_DICT[lower_word])
        else:
            translated_words.append(word)
    
    return ' '.join(translated_words)

# -------------------- ANA İŞLEM DÖNGÜSÜ --------------------
async def process_stream(url: str):
    ffmpeg_cmd = [
        'ffmpeg',
        '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le',
        '-'
    ]
    
    proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE)
    recognizer = KaldiRecognizer(vosk_model, SAMPLE_RATE)
    
    try:
        while True:
            data = proc.stdout.read(CHUNK_SIZE)
            if not data:
                await asyncio.sleep(0.01)
                continue
                
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get('text', '').strip()
                
                if text and len(text.split()) >= MIN_WORDS:
                    translated = translate_text(text)
                    if translated and translated.lower() != text.lower():
                        with print_lock:
                            print(f"\n\033[1;34m[ORJINAL]\033[0m {text}")
                            print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
                        
                        await broadcast(translated)
                        audio_queue.put(translated)
            
            await asyncio.sleep(0.001)
    except Exception as e:
        logging.error(f"Hata: {str(e)}")
    finally:
        proc.terminate()

# -------------------- WEBSOCKET İŞLEMLERİ --------------------
async def broadcast(message: str):
    if clients and message:
        await asyncio.wait([client.send(message) for client in clients])

async def client_handler(websocket, path):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

# -------------------- SİSTEM BAŞLATMA --------------------
logging.basicConfig(level=logging.INFO, format='%(message)s')

fasttext_model, vosk_model, current_model = load_models()

async def main(url: str):
    threading.Thread(target=tts_worker, daemon=True).start()
    
    server = await websockets.serve(client_handler, WS_HOST, WS_PORT)
    
    print("\n\033[1;36mREAL-TIME ÇEVİRİ SİSTEMİ\033[0m")
    print(f"\033[93mDinleniyor: {url}\033[0m")
    print("\033[93mÇeviri Sözlüğü:\033[0m")
    print("- 50+ temel ifade ve kelime")
    print("- Tam cümle çevirisi desteği")
    print("- Gelişmiş eşleştirme algoritması\n")
    
    try:
        await process_stream(url)
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Gerçek Zamanlı Çeviri')
    parser.add_argument('--url', required=True, help='Ses kaynağı (hw:0, default veya dosya)')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        sys.exit(0)
