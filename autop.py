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
import requests

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
MIN_BUFFER_LENGTH = 20  # Minimum çeviri yapılacak uzunluk
SENTENCE_ENDERS = {'.', '!', '?'}
PAUSE_THRESHOLD = 1.5  # Cümle sonu için saniye bekletme
CONTEXT_WINDOW_SIZE = 3  # Bağlam için saklanacak cümle sayısı
LIBRETRANSLATE_URL = "https://libretranslate.com/translate"

# Yükleme kontrolleri
assert os.path.exists(VOSK_MODEL_PATH), "Vosk modeli eksik!"
assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"

model = Model(VOSK_MODEL_PATH)
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
translator = Translator()
clients = set()
tts_queue = Queue()
last_processed_time = time.time()
context_buffer = deque(maxlen=CONTEXT_WINDOW_SIZE)  # Bağlam için cümle bufferı

# Özel terimler sözlüğü
TERM_DICTIONARY = {
    ('en', 'tr'): {
        'machine learning': 'makine öğrenmesi',
        'neural network': 'yapay sinir ağı',
        'accuracy': 'doğruluk',
        'server': 'sunucu',
        'framework': 'çatı',
        'API': 'Uygulama Programlama Arayüzü',
        'cloud': 'bulut',
        'database': 'veritabanı',
        'algorithm': 'algoritma',
        'debug': 'hata ayıklama',
        'interface': 'arayüz',
        'authentication': 'kimlik doğrulama',
        'encryption': 'şifreleme'
    }
}

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

def detect_language(text):
    """Gelişmiş dil tespiti"""
    # Çok kısa metinler için varsayılan İngilizce
    if len(text.split()) < 2:
        return 'en'
    
    # FastText ile tahmin
    lang_pred = fasttext_model.predict(text, k=2)
    lang, score = lang_pred[0][0].replace("__label__", ""), lang_pred[1][0]
    
    # Yeterli güven olmazsa varsayılan dil
    if score < 0.6:
        # Türkçe karakter kontrolü
        if any(char in text.lower() for char in ['ç', 'ğ', 'ı', 'ö', 'ş', 'ü']):
            return 'tr'
        return 'en'
    
    return lang

def is_sentence_complete(text):
    """Metnin cümle yapısını gelişmiş kontrol"""
    # Son karakter cümle sonu işareti mi?
    if len(text) > 0 and text[-1] in SENTENCE_ENDERS:
        return True
    
    # Soru kelimeleri kontrolü
    question_words = {'who', 'what', 'where', 'when', 'why', 'how', 'which', 'whose', 'whom'}
    last_words = text.split()[-3:]
    if any(word.lower() in question_words for word in last_words) and '?' in text:
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

def apply_term_dictionary(text, src_lang, dest_lang):
    """Özel terimler sözlüğünü uygular"""
    dictionary = TERM_DICTIONARY.get((src_lang, dest_lang), {})
    for term, translation in dictionary.items():
        # Büyük/küçük harf duyarlı olmadan değiştir
        text = re.sub(rf'\b{re.escape(term)}\b', translation, text, flags=re.IGNORECASE)
    return text

def correct_turkish_grammar(text):
    """Türkçe dilbilgisi düzeltmeleri"""
    # "ki" bağlacının düzeltilmesi
    text = re.sub(r'\b([a-zğışçöü]+) ki\b', r'\1ki', text, flags=re.IGNORECASE)
    
    # "de/da" bağlacının düzeltilmesi
    text = re.sub(r'\b([a-zğışçöü]+) de\b', r'\1de', text, flags=re.IGNORECASE)
    text = re.sub(r'\b([a-zğışçöü]+) da\b', r'\1da', text, flags=re.IGNORECASE)
    
    # Fiil çekimleri için temel düzeltmeler
    corrections = {
        r' eder\b': ' eder',
        r' etmek\b': ' etmek',
        r' yapmak\b': ' yapmak',
        r' yapar\b': ' yapar',
    }
    
    for pattern, replacement in corrections.items():
        text = re.sub(pattern, replacement, text)
    
    return text

def translate_with_fallback(text, src_lang, dest_lang='tr'):
    """Google Translate başarısız olursa LibreTranslate fallback"""
    try:
        # Önce Google Translate deneyelim
        translated = translator.translate(text, src=src_lang, dest=dest_lang).text
        
        # Çeviri başarısız olursa (orjinal metin dönmüşse) LibreTranslate kullan
        if translated == text or len(translated.split()) < 2:
            raise Exception("Google Translate returned original text")
            
        return translated
    except:
        try:
            # LibreTranslate fallback
            params = {
                'q': text,
                'source': src_lang,
                'target': dest_lang,
                'format': 'text'
            }
            response = requests.post(LIBRETRANSLATE_URL, data=params)
            if response.ok:
                return response.json().get('translatedText', text)
        except Exception as e:
            logger.error(f"Fallback çeviri hatası: {e}")
            
        return text

async def process_translation(buffer):
    """Gelişmiş çeviri işlemini gerçekleştirir"""
    global last_processed_time, context_buffer
    
    try:
        clean_input = clean_text(buffer)
        if not clean_input:
            return ""

        # Dil tahmini
        detected_lang = detect_language(clean_input)
        
        # Bağlam ekleme
        context = " ".join(context_buffer) + " " + clean_input if context_buffer else clean_input
        
        # Çeviri yap (bağlamla birlikte)
        translated = translate_with_fallback(
            context if len(context) < 500 else clean_input,
            detected_lang,
            'tr'
        )
        
        # Sadece yeni kısmı al (bağlam etkisini koruyarak)
        if context_buffer:
            context_len = len(" ".join(context_buffer).split())
            translated_words = translated.split()
            translated = " ".join(translated_words[context_len:])
        
        # Özel terimleri uygula
        translated = apply_term_dictionary(translated, detected_lang, 'tr')
        
        # Dilbilgisi düzeltmeleri
        translated = correct_turkish_grammar(translated)
        translated = clean_text(translated)
        
        # Buffer güncelleme
        context_buffer.append(clean_input)
        
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
    await asyncio.gather(server)
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
