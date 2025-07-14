# backend.py – Whisper + Google Translate + gTTS
# Ses dosyasini alir, metne cevirir, Turkce'ye cevirir ve seslendirir

import os
import tempfile
import whisper
from gtts import gTTS
from googletrans import Translator
import subprocess

# === Bilesenler ===
model = whisper.load_model("tiny")
translator = Translator()

def transcribe_whisper(filepath):
    print("[+] Whisper ile ses cozuluyor...")
    result = model.transcribe(filepath, fp16=False, language=None)
    return result['text']

def translate_to_turkish(text):
    print("[+] Metin ceviriliyor...")
    result = translator.translate(text, dest='tr')
    return result.text

def speak_text(text):
    print("[+] TTS: Metin seslendiriliyor...")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
        tts = gTTS(text=text, lang='tr')
        tts.save(f.name)
        os.system(f"mpg123 -q {f.name}")

def process_file(filepath):
    print(f"[+] Dosya isleniyor: {filepath}")
    raw_text = transcribe_whisper(filepath)
    print("[STT]:", raw_text)

    translated = translate_to_turkish(raw_text)
    print("[Translated]:", translated)

    speak_text(translated)

# === Ornek kullanim ===
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Whisper + Translate + gTTS")
    parser.add_argument("--file", required=True, help="Ses dosyasi (mp3, wav, m4a vs.)")
    args = parser.parse_args()

    process_file(args.file)
