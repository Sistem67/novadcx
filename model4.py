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
TARGET_INTERVAL = 3.5  # Optimal cümle aralığı
MIN_WORDS = 4  # Çeviri için minimum kelime sayısı
MAX_WORDS = 14  # Maksimum kelime sayısı

# Model yolları
MODEL_BASE_DIR = "/root/"
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
FASTTEXT_MODEL = "lid.176.bin"

# Çeviri API ayarları
TRANSLATE_API = "https://libretranslate.de/translate"
TRANSLATE_TIMEOUT = 3
FALLBACK_API = "https://translate.googleapis.com/translate_a/single"
FALLBACK_TIMEOUT = 2

# -------------------- MODEL YÜKLEME --------------------
def load_models():
    """Modelleri yükler ve doğrular"""
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

# -------------------- LOGGING KONFİGÜRASYONU --------------------
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
    """Ses dosyasını oynatır"""
    try:
        with sf.SoundFile(io.BytesIO(data), 'r') as f:
            sd.play(f.read(dtype='float32'), f.samplerate)
            sd.wait()
    except Exception as e:
        logger.error(f"Ses oynatma hatası: {str(e)}")

def tts_worker():
    """Metinden sese dönüşüm thread'i"""
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
async def high_accuracy_translate(text, detected_lang):
    """
    Yüksek doğruluklu çeviri fonksiyonu
    Önce birincil API'yi dener, başarısız olursa fallback kullanır
    """
    # Türkçe modelde bypass
    if 'tr' in current_model.lower():
        return text
    
    # Bağlam ekleme
    context = " ".join(context_history) if context_history else ""
    full_text = f"{context} {text}" if context else text
    
    # Birincil çeviri API'si
    try:
        params = {
            'q': full_text,
            'source': detected_lang,
            'target': 'tr',
            'format': 'text'
        }
        
        response = await asyncio.to_thread(
            requests.post,
            TRANSLATE_API,
            data=params,
            timeout=TRANSLATE_TIMEOUT
        )
        
        if response.status_code == 200:
            translated = response.json().get('translatedText', '')
            # Bağlamdan arındır ve temizle
            clean_translation = translated.replace(context, '').strip() if context else translated
            return clean_translation
            
    except Exception as e:
        logger.debug(f"Birincil çeviri API hatası: {str(e)}")
    
    # Fallback çeviri API'si
    try:
        params = {
            'client': 'gtx',
            'sl': detected_lang,
            'tl': 'tr',
            'dt': 't',
            'q': text  # Fallback'te bağlam kullanmıyoruz
        }
        
        response = await asyncio.to_thread(
            requests.get,
            FALLBACK_API,
            params=params,
            timeout=FALLBACK_TIMEOUT
        )
        
        if response.status_code == 200:
            # Google API yanıtını işle
            translated = ''.join([x[0] for x in response.json()[0] if x[0]])
            return translated
            
    except Exception as e:
        logger.debug(f"Fallback çeviri API hatası: {str(e)}")
    
    # Son çare olarak basit sözlük kullan
    return await simple_dictionary_translate(text)

async def simple_dictionary_translate(text):
    """Basit sözlük tabanlı çeviri (acil durum)"""
    dictionary = {
        'hello': 'merhaba',
        'world': 'dünya',
        'thank you': 'teşekkürler',
        'thanks': 'teşekkürler',
        'good': 'iyi',
        'bad': 'kötü',
        'yes': 'evet',
        'no': 'hayır',
        'please': 'lütfen',
        'sorry': 'üzgünüm',
        'help': 'yardım',
        'what': 'ne',
        'where': 'nerede',
        'when': 'ne zaman',
        'why': 'neden',
        'how': 'nasıl'
    }
    
    # Regex ile tam kelime eşleşmesi yap
    for eng, tr in dictionary.items():
        text = re.sub(rf'\b{eng}\b', tr, text, flags=re.IGNORECASE)
    return text

# -------------------- ANA İŞLEM DÖNGÜSÜ --------------------
async def process_stream(url):
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
    word_count = 0
    
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
                    word_count += len(text.split())
                    current_time = time.time()
                    
                    # Cümle tamamlık kontrolü
                    sentence_complete = any(text.endswith(punct) for punct in ('.', '!', '?'))
                    time_elapsed = current_time - last_time
                    
                    if (sentence_complete or 
                        time_elapsed >= TARGET_INTERVAL or 
                        word_count >= MAX_WORDS):
                        
                        full_text = ' '.join(text_buffer)
                        if len(full_text.split()) >= MIN_WORDS:  # Minimum kelime kontrolü
                            detected_lang = detect_language(full_text)
                            translated = await high_accuracy_translate(full_text, detected_lang)
                            
                            if translated:
                                with print_lock:
                                    print(f"\n\033[1;34m[ORJINAL] ({detected_lang.upper()})\033[0m {full_text}")
                                    print(f"\033[1;32m[ÇEVİRİ] (TR)\033[0m {translated}\n")
                                
                                await broadcast(translated)
                                audio_queue.put(translated)
                                context_history.append(full_text)
                        
                        text_buffer = []
                        word_count = 0
                        last_time = current_time
                        
            await asyncio.sleep(0.005)
    except Exception as e:
        logger.error(f"Ses işleme hatası: {str(e)}")
    finally:
        proc.terminate()

# -------------------- YARDIMCI FONKSİYONLAR --------------------
def detect_language(text):
    """Metnin dilini tespit eder"""
    try:
        predictions = fasttext_model.predict(text.replace("\n", " "), k=1)
        if predictions[1][0] >= 0.5:  # Minimum güven eşiği
            return predictions[0][0].replace('__label__', '')
    except Exception as e:
        logger.debug(f"Dil tespit hatası: {str(e)}")
    return 'en'  # Varsayılan dil

async def broadcast(message):
    """WebSocket istemcilerine mesaj yayınlar"""
    if clients:
        await asyncio.wait([ws.send(message) for ws in clients], timeout=0.5)

async def client_handler(ws, path):
    """WebSocket istemci yöneticisi"""
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.remove(ws)

# -------------------- ANA FONKSİYON --------------------
async def main(url):
    """Ana uygulama fonksiyonu"""
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
    print("- Yüksek doğruluklu çeviri")
    print("- Çift katmanlı API fallback")
    print("- Bağlam duyarlı çeviri")
    print("- Gerçek zamanlı dil tespiti")
    print(f"- {TARGET_INTERVAL}s'lik optimal aralıklar\n")
    
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
    parser = argparse.ArgumentParser(description='Yüksek Doğruluklu Çeviri Sistemi')
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
