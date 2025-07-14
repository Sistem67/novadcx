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
import requests
import sounddevice as sd
import soundfile as sf
import websockets
from gtts import gTTS
from vosk import KaldiRecognizer, Model

# -------------------- KONFİGÜRASYON --------------------
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000  # Daha küçük chunk'lar daha hızlı işlem
MIN_WORDS = 2
MAX_WORDS = 10
MAX_HISTORY = 5  # Son çevirileri saklama sayısı

# Model yolları
MODEL_BASE_DIR = "/root/"
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
FASTTEXT_MODEL = "lid.176.bin"

# Çeviri API
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
TIMEOUT = 1.0  # Daha kısa timeout

# WebSocket
WS_HOST = "0.0.0.0"
WS_PORT = 8000

# -------------------- GLOBAL DEĞİŞKENLER --------------------
clients: Set[websockets.WebSocketServerProtocol] = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()
translation_history: deque = deque(maxlen=MAX_HISTORY)  # Son çevirileri saklar

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
            if text:  # Boş metin kontrolü
                with io.BytesIO() as f:
                    tts = gTTS(text=text, lang='tr', slow=False)
                    tts.write_to_fp(f)
                    play_audio(f.getvalue())
        except Exception as e:
            logging.error(f"TTS hatası: {str(e)}")
        finally:
            audio_queue.task_done()

# -------------------- ÇEVİRİ MOTORU --------------------
async def smart_translate(text: str, src_lang: str) -> Optional[str]:
    """Akıllı çeviri motoru - tekrarları önler"""
    # Aynı metnin tekrar çevirisini önle
    if text.lower() in [t.lower() for t in translation_history]:
        return None
        
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
            
            # Gelişmiş dil bilgisi düzeltmeleri
            grammar_fixes = {
                r'\bI am\b': 'Ben', r'\byou are\b': 'sen', r'\bhe is\b': 'o',
                r'\bwe are\b': 'biz', r'\bthey are\b': 'onlar',
                r'\bI\'m\b': 'Ben', r'\byou\'re\b': 'sen', r'\bhe\'s\b': 'o',
                r'\bI\b': 'Ben', r'\byou\b': 'sen', r'\bhe\b': 'o',
                r'\bshe\b': 'o', r'\bit\b': 'o', r'\bwe\b': 'biz',
                r'\bthey\b': 'onlar'
            }
            
            for eng, tr in grammar_fixes.items():
                translated = re.sub(eng, tr, translated, flags=re.IGNORECASE)
            
            translated = translated.strip()
            translation_history.append(text)  # Çeviri geçmişine ekle
            return translated
            
    except Exception as e:
        logging.debug(f"Çeviri hatası: {str(e)}")
    
    return await basic_translate(text)

async def basic_translate(text: str) -> Optional[str]:
    """Temel sözlük çevirisi"""
    dictionary = {
        r'\bhello\b': 'merhaba', r'\bhi\b': 'selam',
        r'\byes\b': 'evet', r'\bno\b': 'hayır',
        r'\bthank you\b': 'teşekkürler', r'\bthanks\b': 'teşekkürler',
        r'\bplease\b': 'lütfen', r'\bsorry\b': 'üzgünüm',
        r'\bwhat\b': 'ne', r'\bwhere\b': 'nerede', r'\bwhen\b': 'ne zaman',
        r'\bwhy\b': 'neden', r'\bhow\b': 'nasıl', r'\bgood\b': 'iyi', 
        r'\bbad\b': 'kötü', r'\bname\b': 'isim', r'\bmy\b': 'benim',
        r'\byour\b': 'senin', r'\bme\b': 'beni', r'\byou\b': 'seni'
    }
    
    translated = text
    for eng, tr in dictionary.items():
        translated = re.sub(eng, tr, translated, flags=re.IGNORECASE)
    
    return translated if translated != text else None

# -------------------- ANA İŞLEM DÖNGÜSÜ --------------------
async def process_stream(url: str):
    """Optimize edilmiş ses akış işleme"""
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
                await asyncio.sleep(0.005)
                continue
                
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                partial_text = result.get('text', '').strip()
                
                if partial_text:
                    current_text = partial_text
                    word_count = len(current_text.split())
                    
                    # Kısmi sonuçları daha hızlı işle
                    if word_count >= MIN_WORDS:
                        detected_lang = detect_language(current_text)
                        translated = await smart_translate(current_text, detected_lang)
                        
                        if translated:
                            with print_lock:
                                print(f"\n\033[1;34m[ORJINAL]\033[0m {current_text}")
                                print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
                            
                            await broadcast(translated)
                            audio_queue.put(translated)
                        
                        current_text = ""
                        last_update = time.time()
            
            # Kısmi sonuçları kontrol et
            elif time.time() - last_update > 0.5 and current_text:  # 500ms'de bir kontrol
                partial_result = json.loads(recognizer.PartialResult())
                partial_text = partial_result.get('partial', '').strip()
                
                if partial_text and partial_text != current_text:
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
    
    print("\n\033[1;36mGELİŞMİŞ GERÇEK ZAMANLI ÇEVİRİ SİSTEMİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print(f"\033[93mModel: {current_model}\033[0m")
    print("\033[93mÖzellikler:\033[0m")
    print("- Optimize edilmiş gerçek zamanlı çeviri")
    print("- Tekrar önleyici mekanizma")
    print("- WebSocket desteği (ws://{WS_HOST}:{WS_PORT})")
    print("- Kısmi sonuç işleme")
    print("- Gelişmiş dil bilgisi düzeltmeleri\n")
    
    try:
        await process_stream(url)
    except asyncio.CancelledError:
        logging.info("Servis durduruluyor...")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Gelişmiş Gerçek Zamanlı Çeviri Sistemi')
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
