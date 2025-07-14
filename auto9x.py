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
from collections import deque, defaultdict
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
TARGET_INTERVAL = 3.5
MIN_WORDS = 3
MAX_WORDS = 15

# Model yolları
MODEL_BASE_DIR = "/root/"
DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
FASTTEXT_MODEL = "lid.176.bin"

# Çeviri API
PRIMARY_API = "https://libretranslate.de/translate"
FALLBACK_API = "https://translate.googleapis.com/translate_a/single"
TIMEOUT = 3

# -------------------- ÖNBELLEK VE BAĞLAM --------------------
class TranslationMemory:
    def __init__(self):
        self.phrase_cache = defaultdict(str)
        self.context_buffer = deque(maxlen=5)
        self.quality_scores = defaultdict(int)

    def add_translation(self, src, trg):
        self.phrase_cache[src] = trg
        self.context_buffer.append(src)
        self.quality_scores[src] += 1

    def get_cached(self, text):
        if text in self.phrase_cache:
            return self.phrase_cache[text]
        
        # Kısmi eşleşme kontrolü (düzeltildi)
        input_words = set(text.lower().split())
        for phrase in self.phrase_cache:
            phrase_words = set(phrase.lower().split())
            common = input_words & phrase_words
            if len(common) / len(input_words) > 0.6:  # %60 benzerlik
                return self.phrase_cache[phrase]
        return None

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
            with io.BytesIO() as f:
                tts = gTTS(text=text, lang='tr', slow=False)
                tts.write_to_fp(f)
                play_audio(f.getvalue())
        except Exception as e:
            logging.error(f"TTS hatası: {str(e)}")
        finally:
            audio_queue.task_done()

# -------------------- ÇEVİRİ MOTORU --------------------
class TranslationEngine:
    def __init__(self):
        self.memory = TranslationMemory()
        self.consecutive_failures = 0

    async def translate(self, text, detected_lang):
        if 'tr' in current_model.lower():
            return text
            
        cached = self.memory.get_cached(text)
        if cached:
            return cached
            
        if self._is_low_quality(text):
            return await self._fallback_translate(text, detected_lang)
            
        try:
            translated = await self._primary_translate(text, detected_lang)
            if self._validate_translation(text, translated):
                self.memory.add_translation(text, translated)
                self.consecutive_failures = 0
                return translated
        except Exception as e:
            logging.debug(f"API hatası: {str(e)}")
            
        return await self._fallback_translate(text, detected_lang)

    async def _primary_translate(self, text, lang):
        context = " ".join(self.memory.context_buffer)
        full_text = f"{context} {text}" if context else text
        
        params = {
            'q': full_text,
            'source': lang,
            'target': 'tr',
            'format': 'text'
        }
        
        response = await asyncio.to_thread(
            requests.post,
            PRIMARY_API,
            data=params,
            timeout=TIMEOUT
        )
        
        if response.status_code == 200:
            result = response.json().get('translatedText', '')
            return result.replace(context, '').strip() if context else result
        return ""

    async def _fallback_translate(self, text, lang):
        try:
            params = {
                'client': 'gtx',
                'sl': lang,
                'tl': 'tr',
                'dt': 't',
                'q': text
            }
            
            response = await asyncio.to_thread(
                requests.get,
                FALLBACK_API,
                params=params,
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                return ''.join([x[0] for x in response.json()[0] if x[0]])
        except:
            pass
        return await self._dictionary_translate(text)

    async def _dictionary_translate(self, text):
        dictionary = {
            'hello': 'merhaba', 'hi': 'selam',
            'good morning': 'günaydın', 
            'good evening': 'iyi akşamlar',
            'good night': 'iyi geceler',
            'thank you': 'teşekkür ederim',
            'thanks': 'teşekkürler',
            'please': 'lütfen',
            'sorry': 'üzgünüm',
            'yes': 'evet',
            'no': 'hayır',
            'what': 'ne',
            'where': 'nerede',
            'when': 'ne zaman',
            'why': 'niye',
            'how': 'nasıl'
        }
        
        lower_text = text.lower()
        for eng, tr in dictionary.items():
            if eng in lower_text:
                return text.replace(eng, tr)
        return text

    def _validate_translation(self, original, translated):
        if len(translated) < len(original)/3:
            return False
            
        if len(set(translated.split())) < len(translated.split())/2:
            return False
            
        return True

    def _is_low_quality(self, text):
        if len(text.split()) < 2:
            return True
            
        if re.search(r"[^a-zA-Z0-9\s.,!?']", text):
            return True
            
        return False

# -------------------- ANA İŞLEM --------------------
async def process_stream(url, engine):
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
                    time_elapsed = current_time - last_time
                    word_count = len(' '.join(text_buffer).split())
                    
                    sentence_complete = any(text.endswith(punct) for punct in ('.', '!', '?'))
                    
                    if (sentence_complete or 
                        time_elapsed >= TARGET_INTERVAL or 
                        word_count >= MAX_WORDS):
                        
                        full_text = ' '.join(text_buffer)
                        if len(full_text.split()) >= MIN_WORDS:
                            detected_lang = detect_language(full_text)
                            translated = await engine.translate(full_text, detected_lang)
                            
                            if translated:
                                print(f"\n[ORJINAL] {full_text}")
                                print(f"[ÇEVİRİ] {translated}\n")
                                
                                await broadcast(translated)
                                audio_queue.put(translated)
                        
                        text_buffer = []
                        last_time = current_time
                        
            await asyncio.sleep(0.005)
    except Exception as e:
        logging.error(f"Ses işleme hatası: {str(e)}")
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

# -------------------- SİSTEM BAŞLATMA --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

fasttext_model, vosk_model, current_model = load_models()
clients = set()
audio_queue = Queue(maxsize=5)
engine = TranslationEngine()

async def main(url):
    threading.Thread(target=tts_worker, daemon=True).start()
    
    server = await websockets.serve(
        client_handler,
        "0.0.0.0",
        8000
    )
    
    print("\nGELİŞMİŞ ÇEVİRİ SİSTEMİ")
    print(f"Dinleniyor: {url}")
    print(f"Model: {current_model}")
    print("Özellikler:")
    print("- Bağlam duyarlı çeviri")
    print("- Çeviri önbelleği")
    print("- Çift katmanlı API desteği\n")
    
    try:
        await process_stream(url, engine)
    except asyncio.CancelledError:
        pass
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
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
        print("\nServis durduruldu")
