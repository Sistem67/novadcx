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

# -------------------- KONFİGÜRASYON --------------------
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
TARGET_INTERVAL = 3.5  # 3.5 saniyelik doğal aralık
MIN_WORDS = 4
MAX_WORDS = 14

# Model yolları (DÜZENLENEBİLİR)
MODEL_BASE_DIR = "/root/"  # Tüm modellerin bulunduğu ana dizin
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"  # Varsayılan model
FASTTEXT_MODEL = "lid.176.bin"

# -------------------- MODEL YÜKLEME --------------------
def load_models():
    """Modelleri güvenli şekilde yükler"""
    try:
        # Fasttext model kontrolü
        ft_path = os.path.join(MODEL_BASE_DIR, FASTTEXT_MODEL)
        if not os.path.exists(ft_path):
            raise FileNotFoundError(f"Fasttext model bulunamadı: {ft_path}")
        
        # Vosk model kontrolü
        vosk_path = os.path.join(MODEL_BASE_DIR, DEFAULT_MODEL)
        if not os.path.exists(vosk_path):
            raise FileNotFoundError(f"Vosk model bulunamadı: {vosk_path}")

        # Model yüklemeleri
        ft_model = fasttext.load_model(ft_path)
        vosk_model = Model(vosk_path)
        
        return ft_model, vosk_model, DEFAULT_MODEL
        
    except Exception as e:
        logger.error(f"Model yükleme hatası: {str(e)}")
        sys.exit(1)

# -------------------- SİSTEM BAŞLATMA --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('AdvancedTranslator')

# Modelleri yükle
fasttext_model, vosk_model, current_model = load_models()
clients = set()
audio_queue = Queue(maxsize=3)
print_lock = threading.Lock()
context_history = deque(maxlen=3)  # Son 3 cümle bağlamı

# -------------------- SES İŞLEME --------------------
def play_audio(data):
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate)
            sd.wait()
    except Exception as e:
        logger.error(f"Ses oynatma hatası: {str(e)}")

def tts_worker():
    while True:
        text = audio_queue.get()
        try:
            with io.BytesIO() as f:
                tts = gTTS(text=text, lang='tr', lang_check=False, slow=False)
                tts.write_to_fp(f)
                play_audio(f.getvalue())
        except Exception as e:
            logger.error(f"TTS hatası: {str(e)}")
        finally:
            audio_queue.task_done()

# -------------------- ÇEVİRİ MOTORU --------------------
async def context_aware_translate(text, detected_lang):
    """Bağlam duyarlı akıllı çeviri"""
    try:
        # Bağlam oluştur
        context = " ".join(context_history) if context_history else ""
        
        # LibreTranslate API
        params = {
            'q': f"{context} {text}" if context else text,
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
            # Bağlamdan arındırılmış son cümleyi al
            return translated.replace(context, '').strip() if context else translated
            
    except Exception as e:
        logger.debug(f"Çeviri hatası: {str(e)}")
    
    # Fallback mekanizması
    return await basic_translate(text, detected_lang)

async def basic_translate(text, detected_lang):
    """Temel çeviri fallback'i"""
    try:
        params = {
            'client': 'gtx',
            'sl': detected_lang,
            'tl': 'tr',
            'dt': 't',
            'q': text
        }
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
    
    # Acil durum sözlüğü
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
        text = re.sub(rf'\b{eng}\b', tr, text, flags=re.IGNORECASE)
    return text

# -------------------- ANA İŞLEM DÖNGÜSÜ --------------------
async def process_stream(url):
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
                    
                    # Cümle tamamlık kontrolü
                    sentence_complete = any(text.endswith(punct) for punct in ('.', '!', '?'))
                    time_elapsed = current_time - last_time
                    
                    if (sentence_complete or 
                        time_elapsed >= TARGET_INTERVAL or 
                        len(text_buffer) >= MAX_WORDS):
                        
                        full_text = ' '.join(text_buffer)
                        detected_lang = detect_language(full_text)
                        
                        # Türkçe modelde bypass
                        if 'tr' in current_model.lower():
                            translated = full_text
                        else:
                            translated = await context_aware_translate(full_text, detected_lang)
                        
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
        logger.error(f"Ses işleme hatası: {str(e)}")
    finally:
        proc.terminate()

# -------------------- YARDIMCI FONKSİYONLAR --------------------
def detect_language(text):
    try:
        predictions = fasttext_model.predict(text.replace("\n", " "), k=1)
        if predictions[1][0] >= 0.5:
            return predictions[0][0].replace('__label__', '')
    except:
        pass
    return 'en'

async def broadcast(message):
    if clients:
        await asyncio.wait([ws.send(message) for ws in clients], timeout=0.5)

async def client_handler(ws, path):
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.remove(ws)

# -------------------- ANA FONKSİYON --------------------
async def main(url):
    # TTS thread'i başlat
    threading.Thread(target=tts_worker, daemon=True).start()
    
    # WebSocket sunucusu
    server = await websockets.serve(
        client_handler,
        "0.0.0.0",
        8000,
        ping_interval=20,
        ping_timeout=10
    )
    
    print("\n\033[1;36mGELİŞMİŞ ÇEVİRİ SİSTEMİ\033[0m")
    print(f"\033[90mDinleniyor: {url}\033[0m")
    print(f"\033[93mModel: {current_model}\033[0m")
    print("\033[93mÖzellikler:\033[0m")
    print("- Bağlam duyarlı çeviri")
    print("- Otomatik dil algılama")
    print(f"- {TARGET_INTERVAL}s'lik doğal aralıklar\n")
    
    try:
        await process_stream(url)
    except asyncio.CancelledError:
        logger.info("Servis durduruluyor...")
    except Exception as e:
        logger.error(f"Kritik hata: {str(e)}")
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Gelişmiş Çeviri Sistemi')
    parser.add_argument('--url', required=True, help='Ses akış URL')
    parser.add_argument('--model', help='Kullanılacak Vosk model adı')
    args = parser.parse_args()
    
    # Model değişikliği
    if args.model:
        model_path = os.path.join(MODEL_BASE_DIR, args.model)
        if os.path.exists(model_path):
            vosk_model = Model(model_path)
            current_model = args.model
        else:
            logger.error(f"Model bulunamadı: {model_path}")
            sys.exit(1)
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n\033[91mServis durduruldu\033[0m")
    except Exception as e:
        logger.error(f"Başlatma hatası: {str(e)}")
