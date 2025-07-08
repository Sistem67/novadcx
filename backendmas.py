import argparse
import subprocess
import json
import os
import fasttext
from vosk import Model, KaldiRecognizer
from transformers import MarianMTModel, MarianTokenizer
from TTS.api import TTS

# Argümanları al
parser = argparse.ArgumentParser(description="Yayından ses al, altyazı ve sesli çeviri yap")
parser.add_argument("--url", type=str, required=True, help="Yayın URL'si")
args = parser.parse_args()

# Ana dizin (çalıştığın dizin)
BASE_DIR = os.getcwd()  # /root/novadc gibi

# MODEL YOLLARI (ANA DİZİNDE)
FASTTEXT_MODEL_PATH = os.path.join(BASE_DIR, "lid.176.bin")
VOSK_MODEL_EN_PATH = os.path.join(BASE_DIR, "vosk-model-small-en-us-0.15")
VOSK_MODEL_TR_PATH = os.path.join(BASE_DIR, "vosk-model-small-tr-0.3")

MARIAN_MODEL_NAME = "Helsinki-NLP/opus-mt-en-tr"
TTS_MODEL_NAME = "tts_models/tr/mai/tacotron2-DDC"

print("[INFO] FastText modeli yükleniyor...")
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)

def load_vosk_model(path):
    if os.path.exists(path):
        print(f"[INFO] Vosk modeli bulundu: {path}")
        return Model(path)
    else:
        print(f"[WARN] Vosk modeli bulunamadı: {path}")
        return None

vosk_en = load_vosk_model(VOSK_MODEL_EN_PATH)
vosk_tr = load_vosk_model(VOSK_MODEL_TR_PATH)

print("[INFO] MarianMT modeli yükleniyor...")
tokenizer = MarianTokenizer.from_pretrained(MARIAN_MODEL_NAME)
translator = MarianMTModel.from_pretrained(MARIAN_MODEL_NAME)

print("[INFO] Coqui TTS modeli yükleniyor...")
tts = TTS(model_name=TTS_MODEL_NAME, progress_bar=False, gpu=False)

print("[INFO] Tüm modeller yüklendi.\n")

def ffmpeg_cmd(url):
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

def detect_language(text):
    labels, scores = fasttext_model.predict(text)
    lang = labels[0].replace("__label__", "")
    conf = scores[0]
    return lang, conf

def translate(text):
    inputs = tokenizer(text, return_tensors="pt", padding=True)
    outputs = translator.generate(**inputs)
    return tokenizer.decode(outputs[0], skip_special_tokens=True)

def speak(text):
    tts.tts_to_file(text=text, file_path="output.wav")
    print("[TTS] output.wav dosyasına ses kaydedildi.")

def get_recognizer(lang, sample_rate=16000):
    if lang == "en" and vosk_en:
        return KaldiRecognizer(vosk_en, sample_rate)
    elif lang == "tr" and vosk_tr:
        return KaldiRecognizer(vosk_tr, sample_rate)
    else:
        print(f"[ERROR] Desteklenmeyen dil veya model yok: {lang}")
        return None

def main():
    process = subprocess.Popen(ffmpeg_cmd(args.url), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buffer = b""
    lang = None
    recognizer = None

    try:
        while True:
            audio = process.stdout.read(4000)
            if not audio:
                break

            buffer += audio

            if not recognizer and len(buffer) > 16000 * 3:
                temp_rec = KaldiRecognizer(vosk_en if vosk_en else vosk_tr, 16000)
                if temp_rec.AcceptWaveform(buffer):
                    sample_text = json.loads(temp_rec.Result()).get("text", "")
                    lang, conf = detect_language(sample_text)
                    print(f"[LANG] Algılanan dil: {lang} (Güven: {conf:.2f})")

                    recognizer = get_recognizer(lang)
                    if not recognizer:
                        print("[ERROR] Tanıma modeli başlatılamadı, çıkılıyor.")
                        break
                    recognizer.SetWords(True)
                    buffer = b""

            elif recognizer:
                if recognizer.AcceptWaveform(audio):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").strip()
                    if text:
                        print("\n[ORIGINAL]:", text)
                        if lang == "en":
                            translated = translate(text)
                            print("[TRANSLATED]:", translated)
                            speak(translated)
                        elif lang == "tr":
                            print("[TURKCE]:", text)

    except KeyboardInterrupt:
        print("\n[INFO] Program durduruldu.")
    finally:
        process.kill()

if __name__ == "__main__":
    main()
