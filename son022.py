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
logger = logging.getLogger('AkıllıTranslator')

# Performans sabitleri
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
TARGET_INTERVAL = 3.5
MIN_WORDS = 4
MAX_WORDS = 14

# Model yolları
VOSK_MODEL_DIR = "/root/"
ACTIVE_MODEL = "vosk-model-en-us-0.22"  # Varsayılan İngilizce model
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Global nesneler
model = Model(os.path.join(VOSK_MODEL_DIR, ACTIVE_MODEL))
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
clients = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()
context_history = deque(maxlen=3)  # Son 3 cümle bağlamı

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

async def smart_translate(text, detected_lang):
    """Akıllı bağlam duyarlı çeviri"""
    # 1. Bağlam oluştur
    full_context = "\n".join(context_history) + "\n" + text if context_history else text
    
    # 2. LibreTranslate ile bağlam duyarlı çeviri
    try:
        params = {
            'q': full_context,
            'source': detected_lang,
            'target': 'tr',
            'format': 'text'
        }
        response = await asyncio.to_thread(
            requests.post,
            "https://libretranslate.de/translate",
            data=params,
            timeout=3
        )
        if response.status_code == 200:
            translated = response.json().get('translatedText', '')
            # Sadece son cümlenin çevirisini al
            return translated.split('\n')[-1].strip()
    except Exception as e:
        logger.debug(f"Çeviri hatası: {e}")

    # 3. Fallback mekanizması
    return await basic_translate(text, detected_lang)

async def basic_translate(text, detected_lang):
    """Temel çeviri fallback'i"""
    try:
        params = {'client': 'gtx', 'sl': detected_lang, 'tl': 'tr', 'dt': 't', 'q': text}
        response = await asyncio.to_thread(
            requests.get,
            "https://translate.googleapis.com/translate_a/single",
            params=params,
            timeout=2
        )
        if response.status_code == 200:
            return ''.join([x[0] for x in response.json()[0] if x[0]])
    except:
        pass
    
    # Son çare
    dictionary = {
        'hello': 'merhaba',
        'world': 'dünya',
        'thank you': 'teşekkürler',
        'good': 'iyi',
        'bad': 'kötü'
    }
    for eng, tr in dictionary.items():
        text = re.sub(rf'\b{re.escape(eng)}\b', tr, text, flags=re.IGNORECASE)
    return text

def detect_language(text):
    """Gelişmiş dil algılama"""
    try:
        predictions = fasttext_model.predict(text.replace("\n", " "), k=1)
        if predictions[1][0] >= 0.5:
            return predictions[0][0].replace('__label__', '')
    except:
        pass
    return 'en'  # Varsayılan

async def process_stream(url):
    """Akıllı işlem döngüsü"""
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
                    
                    # Cümle tamamlanma kontrolü
                    sentence_complete = any(text.endswith(punct) for punct in ('.', '!', '?'))
                    time_elapsed = current_time - last_time
                    
                    if (sentence_complete and len(text_buffer) >= MIN_WORDS) or \
                       (time_elapsed >= TARGET_INTERVAL and len(text_buffer) >= MIN_WORDS) or \
                       (len(text_buffer) >= MAX_WORDS):
                        
                        full_text = ' '.join(text_buffer)
                        detected_lang = detect_language(full_text)
                        
                        # Türkçe model kullanıyorsak doğrudan çıktı ver
                        if 'tr' in ACTIVE_MODEL.lower():
                            translated = full_text
                        else:
                            translated = await smart_translate(full_text, detected_lang)
                        
                        if translated:
                            with print_lock:
                                print(f"\n\033[1;34m[ORJINAL] ({detected_lang.upper()})\033[0m {full_text}")
                                print(f"\033[1;32m[ÇEVİRİ] (TR)\033[0m {translated}\n")
                            
                            await broadcast(translated)
                            audio_queue.put(translated)
                            context_history.append(full_text)
                        
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
    
    print("\n\033[1;36mAKILLI ÇEVİRİ SERVİSİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print(f"\033[93mKullanılan Model: {ACTIVE_MODEL}\033[0m")
    print("\033[93mÖzellikler:\033[0m")
    print("- Bağlam duyarlı çeviri")
    print("- Otomatik dil algılama")
    print("- Türkçe modelde bypass özelliği")
    print(f"- {TARGET_INTERVAL}s'lik doğal aralıklar\n")
    
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
    parser = argparse.ArgumentParser(description='Akıllı Çeviri Servisi')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    parser.add_argument('--model', help='Vosk model adı (örn: vosk-model-tr-0.22)')
    args = parser.parse_args()
    
    if args.model:
        model_path = os.path.join(VOSK_MODEL_DIR, args.model)
        if os.path.exists(model_path):
            model = Model(model_path)
            ACTIVE_MODEL = args.model
        else:
            logger.error(f"Model bulunamadı: {model_path}")
            sys.exit(1)
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Başlatma hatası: {e}")
