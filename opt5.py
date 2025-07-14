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
from typing import Set

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
MAX_WORDS = 10
MAX_HISTORY = 5

# Model yolları (ORJİNAL HALİYLE)
MODEL_BASE_DIR = "/root/"
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
FASTTEXT_MODEL = "lid.176.bin"

# WebSocket
WS_HOST = "0.0.0.0"
WS_PORT = 8000

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
            raise FileNotFoundError(f"FastText modeli yok: {ft_path}")
        if not os.path.exists(vosk_path):
            raise FileNotFoundError(f"Vosk modeli yok: {vosk_path}")
            
        return (
            fasttext.load_model(ft_path),
            Model(vosk_path),
            DEFAULT_MODEL
        )
    except Exception as e:
        logging.error(f"Model hatası: {str(e)}")
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

# -------------------- ÇEVİRİ MOTORU --------------------
def translate_text(text: str) -> str:
    """Basit ve efektif çeviri"""
    if not text or text.lower() in [t.lower() for t in translation_history]:
        return ""
        
    dictionary = {
        'hello': 'merhaba', 'hi': 'selam',
        'yes': 'evet', 'no': 'hayır',
        'thank you': 'teşekkürler', 'thanks': 'teşekkürler',
        'please': 'lütfen', 'sorry': 'üzgünüm',
        'what': 'ne', 'where': 'nerede', 'when': 'ne zaman',
        'why': 'neden', 'how': 'nasıl', 'good': 'iyi',
        'bad': 'kötü', 'name': 'isim', 'my': 'benim',
        'your': 'senin', 'me': 'beni', 'you': 'seni'
    }
    
    translated = text
    for eng, tr in dictionary.items():
        translated = translated.replace(eng, tr)
    
    if translated != text:
        translation_history.append(text)
        return translated
    return ""

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
                    if translated:
                        with print_lock:
                            print(f"\n[ORJINAL] {text}")
                            print(f"[ÇEVİRİ] {translated}\n")
                        
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
