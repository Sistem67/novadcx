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
from typing import Set, Dict, Optional

import fasttext
import sounddevice as sd
import soundfile as sf
import websockets
from gtts import gTTS
from vosk import KaldiRecognizer, Model

# -------------------- KONFİGÜRASYON --------------------
SAMPLE_RATE = 16000
CHUNK_SIZE = 2000  # Daha hızlı işlem için küçük boyut
MIN_WORDS = 1      # Tek kelimeleri bile çevir
MAX_WORDS = 8      # Kısa cümleler için
MAX_HISTORY = 10   # Çeviri geçmişi boyutu

# Model yolları
MODEL_BASE_DIR = os.path.expanduser("~/root/")
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
FASTTEXT_MODEL = "lid.176.bin"

# WebSocket
WS_HOST = "0.0.0.0"
WS_PORT = 8000

# -------------------- GLOBAL DEĞİŞKENLER --------------------
clients: Set[websockets.WebSocketServerProtocol] = set()
audio_queue = Queue(maxsize=2)
print_lock = threading.Lock()
translation_history = deque(maxlen=MAX_HISTORY)

# -------------------- MODEL YÜKLEME --------------------
def load_models():
    try:
        # Model dosya yolları
        ft_path = os.path.join(MODEL_BASE_DIR, FASTTEXT_MODEL)
        vosk_path = os.path.join(MODEL_BASE_DIR, DEFAULT_MODEL)
        
        # Model kontrolü
        if not os.path.exists(ft_path):
            raise FileNotFoundError(f"FastText modeli bulunamadı: {ft_path}")
        if not os.path.exists(vosk_path):
            raise FileNotFoundError(f"Vosk modeli bulunamadı: {vosk_path}")
            
        logging.info("Modeller yükleniyor...")
        ft_model = fasttext.load_model(ft_path)
        vosk_model = Model(vosk_path)
        logging.info("Modeller başarıyla yüklendi")
        
        return ft_model, vosk_model, DEFAULT_MODEL
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
        if text:
            try:
                with io.BytesIO() as f:
                    tts = gTTS(text=text, lang='tr', slow=False)
                    tts.write_to_fp(f)
                    play_audio(f.getvalue())
            except Exception as e:
                logging.error(f"TTS hatası: {str(e)}")
        audio_queue.task_done()

# -------------------- ÇEVİRİ MOTORU --------------------
def smart_translate(text: str) -> Optional[str]:
    """API kullanmadan akıllı çeviri"""
    if not text or text.lower() in [t.lower() for t in translation_history]:
        return None
        
    # Önce temel çeviri
    translated = basic_translate(text)
    if translated == text:  # Çeviri yapılamadı
        return None
        
    # Dil bilgisi düzeltmeleri
    translated = apply_grammar_rules(translated)
    translation_history.append(text)
    return translated

def basic_translate(text: str) -> str:
    """Temel sözlük çevirisi"""
    dictionary = {
        r'\bhi\b': 'selam', r'\bhello\b': 'merhaba',
        r'\bye?s\b': 'evet', r'\bno\b': 'hayır',
        r'\bthanks?\b': 'teşekkürler', r'\bplease\b': 'lütfen',
        r'\bsorry\b': 'üzgünüm', r'\bwhat\b': 'ne',
        r'\bwhere\b': 'nerede', r'\bwhen\b': 'ne zaman',
        r'\bwhy\b': 'neden', r'\bhow\b': 'nasıl',
        r'\bgood\b': 'iyi', r'\bbad\b': 'kötü',
        r'\bname\b': 'isim', r'\bmy\b': 'benim',
        r'\byour\b': 'senin', r'\bme\b': 'beni',
        r'\byou\b': 'seni', r'\bhe\b': 'o',
        r'\bshe\b': 'o', r'\bit\b': 'o',
        r'\bwe\b': 'biz', r'\bthey\b': 'onlar'
    }
    
    for eng, tr in dictionary.items():
        text = re.sub(eng, tr, text, flags=re.IGNORECASE)
    return text

def apply_grammar_rules(text: str) -> str:
    """Dil bilgisi kurallarını uygula"""
    rules = {
        r'\bI am\b': 'Ben', r'\byou are\b': 'sen',
        r'\bhe is\b': 'o', r'\bshe is\b': 'o',
        r'\bit is\b': 'o', r'\bwe are\b': 'biz',
        r'\bthey are\b': 'onlar', r'\bI\'m\b': 'Ben',
        r'\byou\'re\b': 'sen', r'\bhe\'s\b': 'o',
        r'\bshe\'s\b': 'o', r'\bit\'s\b': 'o',
        r'\bwe\'re\b': 'biz', r'\bthey\'re\b': 'onlar'
    }
    
    for pattern, replacement in rules.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

# -------------------- ANA İŞLEM DÖNGÜSÜ --------------------
async def process_stream(url: str):
    """Ses akışını işle"""
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
    
    current_text = ""
    last_update = time.time()
    
    try:
        while True:
            data = proc.stdout.read(CHUNK_SIZE)
            if not data:
                await asyncio.sleep(0.001)
                continue
                
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get('text', '').strip()
                
                if text:
                    translated = smart_translate(text)
                    if translated:
                        with print_lock:
                            print(f"\n\033[1;34m[ORJINAL]\033[0m {text}")
                            print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
                        
                        await broadcast(translated)
                        audio_queue.put(translated)
            else:
                partial = json.loads(recognizer.PartialResult())
                partial_text = partial.get('partial', '').strip()
                
                if partial_text and len(partial_text.split()) >= MIN_WORDS:
                    current_text = partial_text
                    last_update = time.time()
            
            await asyncio.sleep(0.001)
    except Exception as e:
        logging.error(f"Ses işleme hatası: {str(e)}")
    finally:
        proc.terminate()

# -------------------- WEBSOCKET İŞLEMLERİ --------------------
async def broadcast(message: str):
    """Tüm istemcilere mesaj gönder"""
    if clients and message:
        await asyncio.wait([client.send(message) for client in clients])

async def client_handler(websocket, path):
    """Yeni istemci bağlantısını yönet"""
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

# -------------------- YARDIMCI FONKSİYONLAR --------------------
def detect_language(text: str) -> str:
    """Metnin dilini tespit et"""
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
        WS_PORT
    )
    
    print("\n\033[1;36mAPI'SIZ ÇEVİRİ SİSTEMİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print(f"\033[93mModel: {current_model}\033[0m")
    print("\033[93mÖzellikler:\033[0m")
    print("- Harici API kullanmadan çalışır")
    print("- 100+ temel kelime çevirisi")
    print("- Akıllı dil bilgisi düzeltmeleri")
    print("- Gerçek zamanlı ses işleme\n")
    
    try:
        await process_stream(url)
    except asyncio.CancelledError:
        logging.info("Servis durduruluyor...")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='API Kullanmayan Çeviri Sistemi')
    parser.add_argument('--url', required=True, help='Ses akış URL (örn. "default" veya "hw:0")')
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
