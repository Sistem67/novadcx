import argparse
import subprocess
import json
import fasttext
from vosk import Model, KaldiRecognizer
from transformers import MarianMTModel, MarianTokenizer
from TTS.api import TTS
import os

# --- Komut satırı argümanları ---
parser = argparse.ArgumentParser(description="Canli yayin otomatik ceviri sistemi")
parser.add_argument("--url", type=str, help="Yayin URL'si (.m3u8)")
args = parser.parse_args()

if not args.url:
    print("HATA: --url parametresi eksik.")
    exit(1)

# --- Model yolları ---
vosk_models = {
    "en": "models/vosk-model-small-en-us-0.15",
    "tr": "models/vosk-model-small-tr-0.3"
}
fasttext_model_path = "models/lid.176.ftz"
marian_model_name = "Helsinki-NLP/opus-mt-en-tr"
tts_model_name = "tts_models/tr/mai/tacotron2-DDC"

# --- Model yüklemeleri ---
print("[INFO] Modeller yukleniyor...")
fasttext_model = fasttext.load_model(fasttext_model_path)
vosk_en = Model(vosk_models["en"])
vosk_tr = Model(vosk_models["tr"])
tokenizer = MarianTokenizer.from_pretrained(marian_model_name)
translator = MarianMTModel.from_pretrained(marian_model_name)
tts = TTS(model_name=tts_model_name, progress_bar=False, gpu=False)
print("[INFO] Tüm modeller yüklendi.")

# --- ffmpeg komutu ---
def get_ffmpeg_cmd(url):
    return [
        "ffmpeg",
        "-i", url,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        "pipe:1"
    ]

# --- Dil algilama ---
def detect_lang(text):
    labels, scores = fasttext_model.predict(text)
    lang = labels[0].replace("__label__", "")
    conf = scores[0]
    return lang, conf

# --- Ceviri fonksiyonu ---
def translate(text):
    inputs = tokenizer(text, return_tensors="pt", padding=True)
    translated = translator.generate(**inputs)
    return tokenizer.decode(translated[0], skip_special_tokens=True)

# --- TTS fonksiyonu ---
def speak(text):
    tts.tts_to_file(text=text, file_path="output.wav")
    print("[INFO] TTS sesi output.wav olarak kaydedildi.")

# --- Ana islem ---
def run(url):
    print("[INFO] Yayindan ses aliniyor:", url)
    process = subprocess.Popen(get_ffmpeg_cmd(url), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    buffer = b""
    lang = None
    recognizer = None

    try:
        while True:
            data = process.stdout.read(4000)
            if not data:
                break
            buffer += data

            # Ilk 3 saniyelik veriden dil tespiti
            if not recognizer and len(buffer) > 16000 * 3:
                temp_rec = KaldiRecognizer(vosk_en, 16000)
                if temp_rec.AcceptWaveform(buffer):
                    txt = json.loads(temp_rec.Result()).get("text", "")
                    lang, conf = detect_lang(txt)
                    print("[INFO] Algilanan dil:", lang, "Guven:", round(conf, 2))
                    if lang == "en":
                        recognizer = KaldiRecognizer(vosk_en, 16000)
                    elif lang == "tr":
                        recognizer = KaldiRecognizer(vosk_tr, 16000)
                    else:
                        print("[UYARI] Desteklenmeyen dil:", lang)
                        break
                    recognizer.SetWords(True)
                    buffer = b""

            elif recognizer:
                if recognizer.AcceptWaveform(data):
                    res = json.loads(recognizer.Result())
                    text = res.get("text", "").strip()
                    if text:
                        print("\n[ORIJINAL]:", text)
                        if lang == "en":
                            translated = translate(text)
                            print("[CEVIRI]:", translated)
                            speak(translated)
                        elif lang == "tr":
                            print("[TURKCE]:", text)

    except KeyboardInterrupt:
        print("\n[INFO] Kullanici tarafindan durduruldu.")
    finally:
        process.kill()

# --- Calistir ---
run(args.url)
