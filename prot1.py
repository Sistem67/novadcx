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
import requests
import sounddevice as sd
import soundfile as sf
import websockets
from gtts import gTTS
from vosk import KaldiRecognizer, Model

# -------------------- KONFİGÜRASYON --------------------
SAMPLE_RATE = 16000
CHUNK_SIZE = 8000  # Daha hızlı işlem
TARGET_INTERVAL = 2.0  # Daha agresif aralık
MIN_WORDS = 2
MAX_WORDS = 12

# Model yolları
MODEL_BASE_DIR = "/root/"
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
FASTTEXT_MODEL = "lid.176.bin"

# Çeviri API
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
TIMEOUT = 1.5

# WebSocket
WS_HOST = "0.0.0.0"
WS_PORT = 8000

# -------------------- GLOBAL DEĞİŞKENLER --------------------
clients: Set[websockets.WebSocketServerProtocol] = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()

# -------------------- MODEL YÜKLEME --------------------
def load_models():
    try:
        ft_path = os.path.join(MODEL_BASE_DIR, FASTTEXT_MODEL)
        vosk_path = os.path.join(MODEL_BASE_DIR, DEFAULT_MODEL)
        
        if not os.path.exists(ft_path) or not os.path.exists(vosk_path):
            raise FileNotFoundError("Model dosyaları eksik")
            
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

# -------------------- ÇEVİRİ MOTORU --------------------
async def smart_translate(text: str, src_lang: str) -> str:
    """Akıllı çeviri motoru"""
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
            timeout=TIMEOUT
        )
        
        if response.status_code == 200:
            translated = ''.join([x[0] for x in response.json()[0] if x[0]])
            
            # Temel dil bilgisi düzeltmeleri
            grammar_fixes = {
                "I am": "Ben", "you are": "sen", "he is": "o",
                "we are": "biz", "they are": "onlar",
                "I'm": "Ben", "you're": "sen", "he's": "o"
            }
            
            for eng, tr in grammar_fixes.items():
                translated = translated.replace(eng, tr)
            
            return translated.strip()
    except Exception as e:
        logging.debug(f"Çeviri hatası: {str(e)}")
    
    # Fallback mekanizması
    return await basic_translate(text)

async def basic_translate(text: str) -> str:
    """Temel sözlük çevirisi"""
    dictionary = {
        'hello': 'merhaba', 'hi': 'selam',
        'yes': 'evet', 'no': 'hayır',
        'thank you': 'teşekkürler', 'thanks': 'teşekkürler',
        'please': 'lütfen', 'sorry': 'üzgünüm',
        'what': 'ne', 'where': 'nerede', 'when': 'ne zaman',
        'why': 'neden', 'how': 'nasıl', 'good': 'iyi', 'bad': 'kötü'
    }
    
    for eng, tr in dictionary.items():
        text = re.sub(rf'\b{eng}\b', tr, text, flags=re.IGNORECASE)
    return text

# -------------------- ANA İŞLEM DÖNGÜSÜ --------------------
async def process_stream(url: str):
    """Ses akışını işler ve çevirir"""
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
    
    text_buffer = []
    last_time = time.time()
    
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
                    text_buffer.append(text)
                    current_time = time.time()
                    time_elapsed = current_time - last_time
                    word_count = len(' '.join(text_buffer).split())
                    
                    # Cümle tamamlık kontrolü
                    sentence_complete = any(text.endswith(punct) for punct in ('.', '!', '?'))
                    
                    if (sentence_complete or 
                        time_elapsed >= TARGET_INTERVAL or 
                        word_count >= MAX_WORDS):
                        
                        full_text = ' '.join(text_buffer)
                        if len(full_text.split()) >= MIN_WORDS:
                            detected_lang = detect_language(full_text)
                            translated = await smart_translate(full_text, detected_lang)
                            
                            if translated:
                                with print_lock:
                                    print(f"\n\033[1;34m[ORJINAL]\033[0m {full_text}")
                                    print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
                                
                                await broadcast(translated)
                                audio_queue.put(translated)
                        
                        text_buffer = []
                        last_time = current_time
                        
            await asyncio.sleep(0.001)  # Daha hızlı döngü
    except Exception as e:
        logging.error(f"Ses işleme hatası: {str(e)}")
    finally:
        proc.terminate()

# -------------------- WEBSOCKET İŞLEMLERİ --------------------
async def broadcast(message: str):
    """Tüm istemcilere mesaj gönder"""
    if clients:
        await asyncio.wait([client.send(message) for client in clients])

async def client_handler(websocket: websockets.WebSocketServerProtocol, path: str):
    """Yeni istemci bağlantısını yönet"""
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

# -------------------- YARDIMCI FONKSİYONLAR --------------------
def detect_language(text: str) -> str:
    """Metnin dilini tespit eder"""
    try:
        predictions = fasttext_model.predict(text.replace("\n", " "), k=1)
        if predictions[1][0] >= 0.5:
            return predictions[0][0].replace('__label__', '')
    except:
        pass
    return 'en'

# -------------------- SİSTEM BAŞLATMA --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

fasttext_model, vosk_model, current_model = load_models()

async def main(url: str):
    # TTS thread'i başlat
    threading.Thread(target=tts_worker, daemon=True).start()
    
    # WebSocket sunucusu
    server = await websockets.serve(
        client_handler,
        WS_HOST,
        WS_PORT,
        ping_interval=20,
        ping_timeout=10
    )
    
    print("\n\033[1;36mGELİŞMİŞ ÇEVİRİ SİSTEMİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print(f"\033[93mModel: {current_model}\033[0m")
    print("\033[93mÖzellikler:\033[0m")
    print("- Gerçek zamanlı senkronize çeviri")
    print("- WebSocket desteği (ws://{WS_HOST}:{WS_PORT})")
    print("- Optimize edilmiş cümle işleme")
    print("- Akıllı dil bilgisi düzeltmeleri\n")
    
    try:
        await process_stream(url)
    except asyncio.CancelledError:
        logging.info("Servis durduruluyor...")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Gerçek Zamanlı Çeviri Sistemi')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    parser.add_argument('--model', help='Vosk model adı')
    args = parser.parse_args()
    
    if args.model:
        model_path = os.path.join(MODEL_BASE_DIR, args.model)
        if os.path.exists(model_path):
            vosk_model = Model(model_path)
            current_model = args.model
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
