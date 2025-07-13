import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
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
import numpy as np

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('auto_translation.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Ayarlar
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
VOSK_MODEL_PATH = "/root/vosk-model-small-multi-0.22"  # Çoklu dil desteği
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"
MAX_BUFFER_LENGTH = 70  # Maksimum karakter uzunluğu
MIN_BUFFER_LENGTH = 20  # Minimum çeviri yapılacak uzunluk
SENTENCE_ENDERS = {'.', '!', '?'}
PAUSE_THRESHOLD = 1.8  # Cümle sonu için saniye bekletme
CONTEXT_WINDOW_SIZE = 4  # Bağlam için saklanacak cümle sayısı
LIBRETRANSLATE_URL = "https://libretranslate.com/translate"
LANGUAGE_DETECTION_THRESHOLD = 0.85  # Dil tespiti güven eşiği
MIN_DETECTION_WORDS = 3  # Dil tespiti için minimum kelime sayısı

# Yükleme kontrolleri
assert os.path.exists(VOSK_MODEL_PATH), "Vosk modeli eksik!"
assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"

# Çoklu dil modeli yükleme
model = Model(VOSK_MODEL_PATH)
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)

clients = set()
tts_queue = Queue()
last_processed_time = time.time()
context_buffer = deque(maxlen=CONTEXT_WINDOW_SIZE)  # Bağlam için cümle bufferı
current_language = 'en'  # Geçerli dil
language_confidence = 0.0  # Dil güven skoru
language_history = deque(maxlen=5)  # Son 5 dil tespiti

# Dil eşleme tablosu
LANGUAGE_MAP = {
    'en': 'English',
    'tr': 'Turkish',
    'de': 'German',
    'fr': 'French',
    'es': 'Spanish',
    'it': 'Italian',
    'ru': 'Russian',
    'ar': 'Arabic',
    'zh': 'Chinese',
    'ja': 'Japanese',
    'ko': 'Korean'
}

# Özel terimler sözlüğü
TERM_DICTIONARY = {
    'en': {
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
        'encryption': 'şifreleme',
        'blockchain': 'blok zinciri',
        'artificial intelligence': 'yapay zeka',
        'iot': 'nesnelerin interneti',
        'big data': 'büyük veri'
    },
    'de': {
        'maschinelles lernen': 'makine öğrenmesi',
        'künstliche intelligenz': 'yapay zeka'
    },
    'fr': {
        'apprentissage automatique': 'makine öğrenmesi',
        'intelligence artificielle': 'yapay zeka'
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

def advanced_language_detection(text):
    """Gelişmiş dil tespiti sistemi"""
    # Çok kısa metinler için hızlı çıkış
    words = text.split()
    if len(words) < MIN_DETECTION_WORDS:
        return current_language, language_confidence
    
    # FastText ile dil tespiti
    lang_pred = fasttext_model.predict(text, k=3)
    lang_code = lang_pred[0][0].replace("__label__", "")
    confidence = float(lang_pred[1][0])
    
    # Dil geçmişi ile doğrulama
    if language_history:
        # Son 5 tespitte en yaygın olanı bul
        unique, counts = np.unique(list(language_history), return_counts=True)
        most_common = unique[np.argmax(counts)]
        
        # Mevcut tespit en yaygın dilden farklı ve güven düşükse
        if lang_code != most_common and confidence < 0.9:
            lang_code = most_common
            confidence = max(confidence, 0.85)
    
    logger.info(f"Dil tespiti: {LANGUAGE_MAP.get(lang_code, lang_code)} ({confidence:.2f})")
    return lang_code, confidence

def is_sentence_complete(text):
    """Metnin cümle yapısını gelişmiş kontrol"""
    # Son karakter cümle sonu işareti mi?
    if len(text) > 0 and text[-1] in SENTENCE_ENDERS:
        return True
    
    # Soru kelimeleri kontrolü (çoklu dil desteği)
    question_words = {
        'en': {'who', 'what', 'where', 'when', 'why', 'how', 'which', 'whose', 'whom'},
        'tr': {'kim', 'ne', 'nerede', 'ne zaman', 'niçin', 'nasıl', 'hangi', 'kime', 'kimi'},
        'de': {'wer', 'was', 'wo', 'wann', 'warum', 'wie', 'welche', 'wessen', 'wem'},
        'fr': {'qui', 'quoi', 'où', 'quand', 'pourquoi', 'comment', 'quel', 'à qui', 'lequel'},
        'es': {'quién', 'qué', 'dónde', 'cuándo', 'por qué', 'cómo', 'cuál', 'de quién', 'a quién'},
        'ru': {'кто', 'что', 'где', 'когда', 'почему', 'как', 'какой', 'чей', 'кому'}
    }
    
    last_words = text.split()[-3:]
    words = question_words.get(current_language, question_words['en'])
    
    if any(word.lower() in words for word in last_words) and '?' in text:
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

def apply_term_dictionary(text, src_lang):
    """Özel terimler sözlüğünü uygular"""
    dictionary = TERM_DICTIONARY.get(src_lang, {})
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
    """Akıllı çoklu çeviri motoru entegrasyonu"""
    try:
        # Özel terimleri uygula
        text = apply_term_dictionary(text, src_lang)
        
        # LibreTranslate kullanarak çeviri
        params = {
            'q': text,
            'source': src_lang,
            'target': dest_lang,
            'format': 'text'
        }
        response = requests.post(LIBRETRANSLATE_URL, data=params, timeout=3)
        if response.ok:
            translated = response.json().get('translatedText', text)
            
            # Çeviri kalite kontrolü
            if len(translated.split()) >= len(text.split()) / 2:
                return translated
    except Exception as e:
        logger.error(f"LibreTranslate hatası: {e}")
    
    # Fallback: Google Translate API
    try:
        GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
        params = {
            'client': 'gtx',
            'sl': src_lang,
            'tl': dest_lang,
            'dt': 't',
            'q': text
        }
        response = requests.get(GOOGLE_TRANSLATE_URL, params=params, timeout=3)
        if response.ok:
            result = response.json()
            if result and len(result) > 0:
                # Çeviriyi birleştir
                translated = ''.join([s[0] for s in result[0] if s[0]])
                return translated
    except Exception as e:
        logger.error(f"Google Translate hatası: {e}")
    
    return text

async def process_translation(buffer):
    """Gelişmiş çeviri işlemini gerçekleştirir"""
    global last_processed_time, context_buffer, current_language, language_confidence
    
    try:
        clean_input = clean_text(buffer)
        if not clean_input:
            return ""

        # Bağlam ekleme
        context_text = " ".join(context_buffer) + " " + clean_input if context_buffer else clean_input
        
        # Çeviri yap
        translated = translate_with_fallback(
            context_text if len(context_text) < 500 else clean_input,
            current_language,
            'tr'
        )
        
        # Sadece yeni kısmı al (bağlam etkisini koruyarak)
        if context_buffer:
            context_len = len(" ".join(context_buffer).split())
            translated_words = translated.split()
            translated = " ".join(translated_words[context_len:])
        
        # Dilbilgisi düzeltmeleri
        translated = correct_turkish_grammar(translated)
        translated = clean_text(translated)
        
        # Buffer güncelleme
        context_buffer.append(clean_input)
        
        # Log ve çıktı
        lang_name = LANGUAGE_MAP.get(current_language, current_language)
        logger.info(f"\n[Orjinal - {lang_name}]: {clean_input}")
        logger.info(f"[Türkçe Çeviri]: {translated}")

        await send_subtitle(translated)
        queue_tts(translated)
        
        return translated
    except Exception as e:
        logger.error(f"Çeviri hatası: {e}")
        return ""

# Tanıma ve çeviri
async def recognize_and_translate(url):
    global current_language, language_confidence, language_history
    
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
                
                # Periyodik dil tespiti (her 5 kelimede bir)
                if len(buffer.split()) % 5 == 0 or len(buffer.split()) < 3:
                    detected_lang, confidence = advanced_language_detection(buffer)
                    
                    # Dil geçmişini güncelle
                    language_history.append(detected_lang)
                    
                    # Yeterli güven varsa dili güncelle
                    if confidence > LANGUAGE_DETECTION_THRESHOLD:
                        current_language = detected_lang
                        language_confidence = confidence
                
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
    logger.info("Gelişmiş çok dilli çeviri sistemi aktif")
    await recognize_and_translate(url)

# Çalıştır
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Profesyonel Çok Dilli Otomatik Çeviri Sistemi")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        logger.info("Servis kapatılıyor...")
    except Exception as e:
        logger.error(f"Beklenmeyen hata: {e}")
