import os
import queue
import sounddevice as sd
import numpy as np
import fasttext
from vosk import Model, KaldiRecognizer
from transformers import MarianMTModel, MarianTokenizer
from TTS.api import TTS
import subprocess
import json

# --- AYARLAR ---
vosk_models = {
    "en": "models/vosk/vosk-model-small-en-us-0.15",
    "tr": "models/vosk/vosk-model-small-tr-0.3"
}
fasttext_model_path = "models/fasttext/lid.176.bin"
marian_model_name = "Helsinki-NLP/opus-mt-en-tr"  # İngilizce -> Türkçe
mic_samplerate = 16000
tts_model_name = "pavoque/turkish-female-glow-tts"

# --- YÜKLEMELER ---
print(">> Modeller yükleniyor...")
fasttext_model = fasttext.load_model(fasttext_model_path)
tokenizer = MarianTokenizer.from_pretrained(marian_model_name)
translator = MarianMTModel.from_pretrained(marian_model_name)
tts = TTS(tts_model_name)
q = queue.Queue()

# --- SES YAKALAMA CALLBACK ---
def callback(indata, frames, time, status):
    if status:
        print("HATA:", status)
    q.put(bytes(indata))

# --- KONUŞMA ALGILAMA ve ÇEVİRİ ---
def recognize_and_translate():
    print(">> Dinleniyor (Ctrl+C ile çık)...")
    with sd.RawInputStream(samplerate=mic_samplerate, blocksize=8000, dtype='int16',
                           channels=1, callback=callback):
        rec = None
        vosk_lang = "en"  # varsayılan dil

        while True:
            data = q.get()
            if rec is None:
                # Başlangıçta dil tespiti için örnek al
                sample_audio = np.frombuffer(data, dtype=np.int16).tobytes()
                with open("temp.wav", "wb") as f:
                    subprocess.run(['ffmpeg', '-f', 's16le', '-ar', str(mic_samplerate), '-ac', '1', '-i', '-', 'temp.wav'],
                                   input=sample_audio, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                vosk_lang = detect_language("temp.wav")
                rec = KaldiRecognizer(Model(vosk_models[vosk_lang]), mic_samplerate)

            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                text = result.get("text", "")
                if text:
                    print(f"[Orijinal] ({vosk_lang}):", text)
                    if vosk_lang != "tr":
                        translation = translate_text(text)
                        print("[Türkçe]:", translation)
                        speak_text(translation)
                    else:
                        speak_text(text)

# --- DİL ALGILAMA ---
def detect_language(wav_path):
    subprocess.run(["ffmpeg", "-y", "-i", wav_path, "-ar", "16000", "-ac", "1", "-f", "s16le", "temp.raw"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open("temp.raw", "rb") as f:
        audio = f.read()
    # Sahte metin oluşturulamayacağı için varsayılanı en yapaydan alın
    fake_text = "Hello, how are you doing today?"
    lang = fasttext_model.predict(fake_text)[0][0].replace("__label__", "")
    return lang if lang in vosk_models else "en"

# --- ÇEVİRİ ---
def translate_text(text):
    inputs = tokenizer([text], return_tensors="pt", padding=True)
    translated = translator.generate(**inputs)
    return tokenizer.decode(translated[0], skip_special_tokens=True)

# --- SESLİ OKUMA ---
def speak_text(text):
    print("[Sesli]:", text)
    tts.tts_to_file(text=text, file_path="out.wav")
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "out.wav"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# --- BAŞLAT ---
if __name__ == "__main__":
    try:
        recognize_and_translate()
    except KeyboardInterrupt:
        print("\n>> Çıkıldı.")
