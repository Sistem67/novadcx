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
logger = logging.getLogger('UniversalTranslator')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
TARGET_INTERVAL = 3.5
MIN_WORDS = 3
MAX_WORDS = 12

# Model yolları (KULLANICI TANIMLI)
VOSK_MODEL_DIR = "/root/"  # Tüm modellerin bulunduğu dizin
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"  # Varsayılan model
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Global nesneler
try:
    # Model yükleyici fonksiyonu
    def load_vosk_model(model_name=DEFAULT_MODEL):
        model_path = os.path.join(VOSK_MODEL_DIR, model_name)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model dizini bulunamadı: {model_path}")
        return Model(model_path)

    model = load_vosk_model()  # Varsayılan modelle başlat
    fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
except Exception as e:
    logger.error(f"Model yükleme hatası: {e}")
    sys.exit(1)

clients = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()
current_model = DEFAULT_MODEL

def switch_model(new_model_name):
    """Çalışma zamanında model değiştirme"""
    global model, current_model
    try:
        model = load_vosk_model(new_model_name)
        current_model = new_model_name
        logger.info(f"Model değiştirildi: {new_model_name}")
        return True
    except Exception as e:
        logger.error(f"Model değiştirme hatası: {e}")
        return False

def play_audio(data):
    """Bellekten ses çalma"""
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

async def force_translate_to_turkish(text):
    """4 katmanlı çeviri garantisi"""
    # 1. LibreTranslate denemesi
    try:
        params = {'q': text, 'source': 'auto', 'target': 'tr', 'format': 'text'}
        response = await asyncio.to_thread(
            requests.post,
            "https://libretranslate.de/translate",
            data=params,
            timeout=2
        )
        if response.status_code == 200:
            translated = response.json().get('translatedText')
            if translated and has_turkish_chars(translated):
                return translated
    except:
        pass

    # 2. Google Translate yedeği
    try:
        params = {'client': 'gtx', 'sl': 'auto', 'tl': 'tr', 'dt': 't', 'q': text}
        response = await asyncio.to_thread(
            requests.get,
            "https://translate.googleapis.com/translate_a/single",
            params=params,
            timeout=2
        )
        if response.status_code == 200:
            translated = ''.join([x[0] for x in response.json()[0] if x[0]])
            if has_turkish_chars(translated):
                return translated
    except:
        pass

    # 3. Manuel sözlük fallback
    dictionary = {
        'hello': 'merhaba',
        'world': 'dünya',
        'thank you': 'teşekkürler',
        'good': 'iyi',
        'bad': 'kötü',
        'yes': 'evet',
        'no': 'hayır'
    }
    for eng, tr in dictionary.items():
        text = re.sub(rf'\b{re.escape(eng)}\b', tr, text, flags=re.IGNORECASE)
    
    # 4. Son çare
    return text if text.strip() else "(Çeviri yapılamadı)"

def has_turkish_chars(text):
    """Türkçe karakter kontrolü"""
    turkish_chars = {'ç', 'ğ', 'ı', 'ö', 'ş', 'ü', 'Ç', 'Ğ', 'İ', 'Ö', 'Ş', 'Ü'}
    return any(char in text for char in turkish_chars)

async def process_stream(url):
    """Ana işlem döngüsü"""
    cmd = [
        'ffmpeg', '-i', url, '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-'
    ]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    
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
                
                if text and len(text.split()) >= 1:
                    text_buffer.append(text)
                    current_time = time.time()
                    
                    if (current_time - last_time >= TARGET_INTERVAL or 
                        len(text_buffer) >= MAX_WORDS):
                        
                        full_text = ' '.join(text_buffer)
                        translated = await force_translate_to_turkish(full_text)
                        
                        if translated:
                            with print_lock:
                                print(f"\n\033[1;34m[ORJINAL]\033[0m {full_text}")
                                print(f"\033[1;32m[ÇEVİRİ]\033[0m {translated}\n")
                                print(f"\033[90mKullanılan Model: {current_model}\033[0m")
                            
                            await broadcast(translated)
                            audio_queue.put(translated)
                        
                        text_buffer = []
                        last_time = current_time
                        
            await asyncio.sleep(0.005)
    except Exception as e:
        logger.error(f"Ses işleme hatası: {e}")
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
    
    print("\n\033[1;36mEVRENSEL ÇEVİRİ SERVİSİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print("\033[93mKULLANIM:\033[0m")
    print(f"- Vosk modelleri dizini: {VOSK_MODEL_DIR}")
    print(f"- Varsayılan model: {DEFAULT_MODEL}")
    print("- Çalışırken model değiştirmek için: touch /tmp/switch_to_<model_adi>")
    print(f"- Çeviri aralığı: {TARGET_INTERVAL}s\n")
    
    try:
        # Model değişikliklerini izle
        def watch_for_model_changes():
            while True:
                for f in os.listdir('/tmp'):
                    if f.startswith('switch_to_'):
                        new_model = f.replace('switch_to_', '')
                        os.unlink(os.path.join('/tmp', f))
                        switch_model(new_model)
                time.sleep(5)
        
        threading.Thread(target=watch_for_model_changes, daemon=True).start()
        
        await process_stream(url)
    except asyncio.CancelledError:
        logger.info("Durduruluyor...")
    except Exception as e:
        logger.error(f"Hata: {e}")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Çok Dilli Çeviri Servisi')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    parser.add_argument('--model', help='Vosk model adı (opsiyonel)')
    args = parser.parse_args()
    
    if args.model:
        if not switch_model(args.model):
            sys.exit(1)
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Başlatma hatası: {e}")
