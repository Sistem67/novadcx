import io
import sounddevice as sd
import numpy as np
from queue import Queue
from threading import Event
import whisper
from googletrans import Translator
from gtts import gTTS
import pygame
import time

# ------------------ KONFİGÜRASYON ------------------
SAMPLE_RATE = 16000  # Whisper için ideal örnekleme hızı
CHUNK_SIZE = 1024    # Ses tampon boyutu
LANG_SOURCE = "en"   # Kaynak dil (İngilizce)
LANG_TARGET = "tr"   # Hedef dil (Türkçe)

# ------------------ GLOBAL DEĞİŞKENLER ------------------
audio_queue = Queue()  # Gerçek zamanlı ses verisi için kuyruk
stop_event = Event()   # Thread'leri durdurmak için
translator = Translator()
pygame.mixer.init()    # gTTS ses oynatma için

# ------------------ WHISPER MODEL YÜKLEME ------------------
model = whisper.load_model("small")  # RAM'de tutulan model

def record_audio_callback(indata, frames, time, status):
    """Sounddevice callback fonksiyonu: Mikrofon verisini kuyruğa ekler"""
    audio_queue.put(indata.copy())

def transcribe_audio(buffer):
    """RAM'deki ses verisini metne çevirir (dosya kullanmadan)"""
    audio_np = np.concatenate(buffer)
    audio_float = audio_np.astype(np.float32) / 32767.0  # int16 -> float32 dönüşümü
    
    # BytesIO ile sanal dosya oluştur
    with io.BytesIO() as audio_io:
        # Whisper'ın beklediği format (16kHz, mono, float32)
        np.save(audio_io, audio_float)
        audio_io.seek(0)
        result = model.transcribe(audio_io, language=LANG_SOURCE, fp16=False)
    
    return result["text"]

def translate_text(text):
    """Metni çevirir"""
    try:
        return translator.translate(text, src=LANG_SOURCE, dest=LANG_TARGET).text
    except:
        return "Çeviri hatası"

def text_to_speech(text):
    """Metni sese çevirir (dosya kullanmadan)"""
    with io.BytesIO() as audio_io:
        tts = gTTS(text=text, lang=LANG_TARGET, slow=False)
        tts.write_to_fp(audio_io)
        audio_io.seek(0)
        
        # Pygame ile RAM'den çal
        pygame.mixer.music.load(audio_io)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)

def process_stream():
    """Gerçek zamanlı işlem döngüsü"""
    buffer = []
    print("Sistem aktif! Konuşmaya başlayın... (Çıkış için Ctrl+C)")

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_SIZE,
        callback=record_audio_callback
    ):
        while not stop_event.is_set():
            # Kuyruktan ses verisi al
            while not audio_queue.empty():
                chunk = audio_queue.get()
                buffer.append(chunk)

                # 5 saniyelik veri biriktirince işle
                if len(buffer) >= (SAMPLE_RATE * 5) // CHUNK_SIZE:
                    text = transcribe_audio(buffer)
                    if text.strip():
                        translated = translate_text(text)
                        print(f"Çeviri: {translated}")
                        text_to_speech(translated)
                    buffer.clear()

            time.sleep(0.1)

# ------------------ ÇALIŞTIRMA ------------------
if __name__ == "__main__":
    try:
        process_stream()
    except KeyboardInterrupt:
        stop_event.set()
        print("\nSistem durduruldu.")
