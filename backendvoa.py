import subprocess
import json
import sys
from vosk import Model, KaldiRecognizer
from argostranslate import translate

# VOSK modeli yolu
vosk_model_path = "/root/vosk-model-small-multilingual"

# Argos modeli kontrol
langs = translate.load_installed_languages()
from_lang = next((l for l in langs if l.code == "en"), None)
to_lang = next((l for l in langs if l.code == "tr"), None)

if not from_lang or not to_lang:
    print("Argos çeviri modeli eksik. en_tr.argosmodel yüklenmeli.")
    sys.exit(1)

translate_fn = from_lang.get_translation(to_lang)

# VOSK model kontrol
model = Model(vosk_model_path)
rec = KaldiRecognizer(model, 16000)

# Yayın URL kontrol
if len(sys.argv) < 2:
    print("Kullanım: python3 backend_vosk_argos.py <m3u8_link>")
    sys.exit(1)

url = sys.argv[1]

print("Canlı yayın dinleniyor...\n")

process = subprocess.Popen([
    "ffmpeg", "-i", url,
    "-ar", "16000", "-ac", "1",
    "-f", "s16le", "-loglevel", "quiet", "-"
], stdout=subprocess.PIPE)

while True:
    data = process.stdout.read(4000)
    if len(data) == 0:
        continue

    if rec.AcceptWaveform(data):
        result = json.loads(rec.Result())
        original = result.get("text", "").strip()
        if original:
            translated = translate_fn.translate(original)
            print("Orijinal:", original)
            print("Çeviri :", translated)
            print()
