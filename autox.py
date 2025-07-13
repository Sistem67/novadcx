import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
from googletrans import Translator
import fasttext
import os
from gtts import gTTS
import tempfile
from queue import Queue
import threading
import re
from collections import deque
import logging
import time

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('translation_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Ayarlar
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"
MAX_BUFFER_LENGTH = 60  # Maksimum karakter uzunluğu
MIN_BUFFER_LENGTH = 25  # Minimum çeviri yapılacak uzunluk
SENTENCE_ENDERS = {'.', '!', '?'}
PAUSE_THRESHOLD = 1.5  # Cümle sonu için saniye bekletme

# Yükleme kontrolleri
assert os.path.exists(VOSK_MODEL_PATH), "Vosk modeli eksik!"
assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"

model = Model(VOSK_MODEL_PATH)
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
translator = Translator()
clients = set()
tts_queue = Queue()
last_processed_time = time.time()

# TTS Sesli okuma işçisi
def tts_worker():
    while True:
        text = tts_queue.get()
        if text:
            try:
                with tempfile.NamedTemporaryFile(delete=True, suffix=".mp3") as fp:
                    tts = gTTS(text=text, lang='tr', slow=False)
                    tts.save(fp.name)
                    # Daha doğal ses için mpg123 parametreleri
                    os.system(f"mpg123 -q --gain 3 --mono {fp.name}")
            except Exception as e:
                logger.error(f"TTS Hatası: {e}")
        tts_queue.task_done()

threading.Thread(target=tts_worker, daemon=True).start()

def queue_tts(text):
    """Metni TTS kuyruğuna eklerken öncelik ve tekrar kontrolü"""
    if len(text.strip()) > 15 and not text.isnumeric():
        # Aynı metnin tekrarını önle
        if not tts_queue.queue or text != tts_queue.queue[-1]:
            tts_queue.put(text)

# Altyazı websocket
async def subtitle_server(websocket, path):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

# Altyazıyı gönder
async def send_subtitle(text):
    if clients:
        try:
            await asyncio.wait([client.send(text) for client in clients])
        except Exception as e:
            logger.error(f"WebSocket gönderim hatası: {e}")

# Yayın sesini al
def stream_audio(url):
    return subprocess.Popen([
        'ffmpeg',
        '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le',
        '-'
    ], stdout=subprocess.PIPE).stdout

# Metin temizleme ve normalleştirme
def clean_text(text):
    """
    Metni temizler ve normalleştirir:
    - Gereksiz boşlukları kaldırır
    - Özel karakterleri filtreler
    - Tekrarları önler
    - Büyük/küçük harf düzenlemesi yapar
    """
    # Temel temizlik
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)
    
    # Tekrar eden noktalama işaretlerini temizle
    text = re.sub(r'([.,!?-])\1+', r'\1', text)
    
    # Büyük/küçük harf normalleştirme
    sentences = re.split(r'([.!?] )', text)
    sentences = [s.capitalize() for s in sentences if s]
    text = ''.join(sentences)
    
    return text.strip()

def is_sentence_complete(text):
    """Metnin cümle yapısını kontrol eder"""
    # Son karakter cümle sonu işareti mi?
    if len(text) > 0 and text[-1] in SENTENCE_ENDERS:
        return True
    
    # Uzun süredir yeni kelime gelmedi mi?
    global last_processed_time
    return (time.time() - last_processed_time) > PAUSE_THRESHOLD

def should_process(buffer):
    """Çeviri yapılacak kritere uygun mu kontrol eder"""
    # Minimum uzunluk kontrolü
    if len(buffer) < MIN_BUFFER_LENGTH:
        return False
    
    # Cümle tamamlandı mı?
    if is_sentence_complete(buffer):
        return True
    
    # Maksimum uzunluğa ulaştı mı?
    if len(buffer) >= MAX_BUFFER_LENGTH:
        return True
    
    return False

async def process_translation(buffer):
    """Çeviri işlemini gerçekleştirir"""
    global last_processed_time
    
    try:
        clean_input = clean_text(buffer)
        if not clean_input:
            return ""

        # Dil tahmini
        lang_pred = fasttext_model.predict(clean_input, k=1)
        detected_lang = lang_pred[0][0].replace("__label__", "")

        # Türkçeye çeviri
        translated = translator.translate(
            clean_input, 
            src=detected_lang, 
            dest='tr'
        ).text
        
        translated = clean_text(translated)
        
        # Log ve çıktı
        logger.info(f"\n[Orjinal - {detected_lang}]: {clean_input}")
        logger.info(f"[Türkçe Çeviri]: {translated}")

        await send_subtitle(translated)
        queue_tts(translated)
        
        return translated
    
    except Exception as e:
        logger.error(f"Çeviri hatası: {e}")
        return ""

# Tanıma ve çeviri
async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    buffer = ""
    last_update_time = time.time()

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            original_text = result.get('text', '').strip()

            if original_text:
                global last_processed_time
                last_processed_time = time.time()
                
                # Buffer'a ekleme yaparken akıcılığı koru
                if buffer and not buffer.endswith(' '):
                    buffer += ' '
                buffer += original_text
                
                # Çeviri kriterlerini kontrol et
                if should_process(buffer):
                    translated = await process_translation(buffer)
                    if translated:
                        buffer = ""
                    else:
                        # Çeviri başarısız oldu, buffer'ı kısalt
                        buffer = buffer[-MAX_BUFFER_LENGTH:]

        # Sürekli kontrol için küçük bir bekleme
        await asyncio.sleep(0.1)

# Ana program
async def main(url):
    server = websockets.serve(subtitle_server, "0.0.0.0", 8000)
    await server
    logger.info("WebSocket altyazı servisi 8000 portunda başlatıldı.")
    await recognize_and_translate(url)

# Çalıştır
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Profesyonel Tam Otomatik Çeviri Backend")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        logger.info("Servis kapatılıyor...")
    except Exception as e:
        logger.error(f"Beklenmeyen hata: {e}")
