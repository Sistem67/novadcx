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
from typing import Set, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

import fasttext
import requests
import sounddevice as sd
import soundfile as sf
import websockets
from gtts import gTTS
from vosk import KaldiRecognizer, Model

# -------------------- KONFİGÜRASYON --------------------
SAMPLE_RATE = 16000
CHUNK_SIZE = 2000  # Ultra küçük chunk'lar maksimum hız için
MIN_WORDS = 1      # Tek kelimeleri bile çevir
MAX_WORDS = 8      # Daha kısa cümleler için
MAX_HISTORY = 10   # Çeviri geçmişi
BUFFER_TIME = 0.3  # Ses buffer süresi (saniye)

# Model yolları
MODEL_BASE_DIR = "/root/"
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
FASTTEXT_MODEL = "lid.176.bin"

# Çeviri API
TRANSLATE_API = "https://api.deepseek.com/v1/translate"  # DeepSeek API
TIMEOUT = 0.8  # Ultra hızlı timeout

# WebSocket
WS_HOST = "0.0.0.0"
WS_PORT = 8000

# -------------------- GLOBAL DEĞİŞKENLER --------------------
clients: Set[websockets.WebSocketServerProtocol] = set()
audio_queue = Queue(maxsize=2)  # Daha küçük queue
print_lock = threading.Lock()
translation_history: deque = deque(maxlen=MAX_HISTORY)
audio_buffer = bytearray()
last_processed = time.time()
executor = ThreadPoolExecutor(max_workers=4)  # Paralel işlem için

# -------------------- MODEL YÜKLEME --------------------
def load_models():
    try:
        ft_path = os.path.join(MODEL_BASE_DIR, FASTTEXT_MODEL)
        vosk_path = os.path.join(MODEL_BASE_DIR, DEFAULT_MODEL)
        
        if not os.path.exists(ft_path) or not os.path.exists(vosk_path):
            raise FileNotFoundError("Model dosyaları eksik")
            
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
        if not text:
            continue
            
        try:
            start_time = time.time()
            with io.BytesIO() as f:
                tts = gTTS(text=text, lang='tr', slow=False)
                tts.write_to_fp(f)
                play_audio(f.getvalue())
            logging.debug(f"TTS süresi: {time.time()-start_time:.2f}s")
        except Exception as e:
            logging.error(f"TTS hatası: {str(e)}")
        finally:
            audio_queue.task_done()

# -------------------- ÇEVİRİ MOTORU --------------------
async def deepseek_translate(text: str, src_lang: str = "en") -> Optional[str]:
    """DeepSeek API ile ultra hızlı çeviri"""
    if not text or text.lower() in [t.lower() for t in translation_history]:
        return None
        
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY')}"
        }
        payload = {
            "text": text,
            "source_lang": src_lang,
            "target_lang": "tr",
            "context": "conversation"
        }
        
        start_time = time.time()
        response = await asyncio.to_thread(
            requests.post,
            TRANSLATE_API,
            json=payload,
            headers=headers,
            timeout=TIMEOUT
        )
        
        if response.status_code == 200:
            translated = response.json().get("translated_text", "").strip()
            if translated:
                # Akıllı post-processing
                translated = apply_smart_rules(translated)
                translation_history.append(text)
                logging.debug(f"Çeviri süresi: {time.time()-start_time:.2f}s")
                return translated
    except Exception as e:
        logging.debug(f"DeepSeek çeviri hatası: {str(e)}")
    
    return await fast_translate(text)

async def fast_translate(text: str) -> Optional[str]:
    """Yedek ultra hızlı çeviri"""
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
    
    translated = text
    for eng, tr in dictionary.items():
        translated = re.sub(eng, tr, translated, flags=re.IGNORECASE)
    
    return translated if translated != text else None

def apply_smart_rules(text: str) -> str:
    """Akıllı dil kuralları uygular"""
    rules = {
        r'\bI am\b': 'Ben', r'\byou are\b': 'sen', 
        r'\bhe is\b': 'o', r'\bshe is\b': 'o',
        r'\bit is\b': 'o', r'\bwe are\b': 'biz',
        r'\bthey are\b': 'onlar', r'\bI\'m\b': 'Ben',
        r'\byou\'re\b': 'sen', r'\bhe\'s\b': 'o',
        r'\bshe\'s\b': 'o', r'\bit\'s\b': 'o',
        r'\bwe\'re\b': 'biz', r'\bthey\'re\b': 'onlar',
        r'\bdon\'t\b': 'yapma', r'\bdoesn\'t\b': 'yapmıyor',
        r'\bcan\'t\b': 'yapamam', r'\bcannot\b': 'yapamam',
        r'\bwon\'t\b': 'yapmayacağım', r'\bwouldn\'t\b': 'yapmazdım',
        r'\bisn\'t\b': 'değil', r'\baren\'t\b': 'değil',
        r'\bwasn\'t\b': 'değildi', r'\bweren\'t\b': 'değildiniz'
    }
    
    for pattern, replacement in rules.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    return text

# -------------------- ANA İŞLEM DÖNGÜSÜ --------------------
async def process_stream(url: str):
    """Ultra hızlı ses işleme döngüsü"""
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
                
            # Ses buffer'ını güncelle
            audio_buffer.extend(data)
            
            # Zaman aşımı kontrolü
            if time.time() - last_update > BUFFER_TIME and current_text:
                await process_text(current_text)
                current_text = ""
                last_update = time.time()
            
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get('text', '').strip()
                
                if text:
                    current_text = text
                    await process_text(current_text)
                    current_text = ""
                    last_update = time.time()
            else:
                partial = json.loads(recognizer.PartialResult())
                partial_text = partial.get('partial', '').strip()
                
                if partial_text:
                    current_text = partial_text
                    last_update = time.time()
            
            await asyncio.sleep(0.001)
            
    except Exception as e:
        logging.error(f"Ses işleme hatası: {str(e)}")
    finally:
        proc.terminate()

async def process_text(text: str):
    """Metni işle ve çevir"""
    if not text or len(text.split()) > MAX_WORDS:
        return
        
    detected_lang = detect_language(text)
    translated = await deepseek_translate(text, detected_lang)
    
    if not translated:
        return
        
    with print_lock:
        print(f"\n\033[1;34m[ORJINAL]\033[0m {text}")
        print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
    
    await broadcast(translated)
    audio_queue.put(translated)

# -------------------- WEBSOCKET İŞLEMLERİ --------------------
async def broadcast(message: str):
    """Tüm istemcilere mesaj gönder"""
    if clients and message:
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
    
    print("\n\033[1;36mULTRA HIZLI GERÇEK ZAMANLI ÇEVİRİ SİSTEMİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print(f"\033[93mModel: {current_model}\033[0m")
    print("\033[93mÖzellikler:\033[0m")
    print("- DeepSeek API ile ultra hızlı çeviri")
    print("- Gerçek zamanlı senkronizasyon (<0.5s gecikme)")
    print("- Akıllı tekrar önleme mekanizması")
    print("- Optimize edilmiş ses işleme pipeline'ı")
    print("- Gelişmiş dil bilgisi kuralları\n")
    
    try:
        await process_stream(url)
    except asyncio.CancelledError:
        logging.info("Servis durduruluyor...")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Ultra Hızlı Gerçek Zamanlı Çeviri Sistemi')
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
