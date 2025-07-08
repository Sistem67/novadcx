import argparse
import subprocess
import fasttext
import json
from vosk import Model, KaldiRecognizer
from transformers import MarianMTModel, MarianTokenizer
from TTS.api import TTS

# --- Argümanlar ---
parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True, help="Canlı yayın (m3u8 / stream) ses URL'si")
args = parser.parse_args()
url = args.url

# --- Model yolları (alt dizin yok!) ---
vosk_en_path = "/root/novadc/vosk-model-small-en-us-0.15"
vosk_tr_path = "/root/novadc/vosk-model-small-tr-0.3"
fasttext_model_path = "/root/novadc/lid.176.bin"
marian_model_name = "Helsinki-NLP/opus-mt-en-tr"
tts_model_name = "pavoque/turkish-female-glow-tts"

# --- Modelleri yükle ---
print(">> Modeller yükleniyor...")
fasttext_model = fasttext.load_model(fasttext_model_path)
vosk_en = Model(vosk_en_path)
vosk_tr = Model(vosk_tr_path)
tokenizer = MarianTokenizer.from_pretrained(marian_model_name)
translator = MarianMTModel.from_pretrained(marian_model_name)
tts = TTS(tts_model_name)

# --- Yayını ffmpeg ile oku ---
def stream_audio(url):
    print(f">> Yayın açılıyor: {url}")
    cmd = [
        "ffmpeg", "-i", url,
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ac", "1", "-ar", "16000", "-"
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

# --- Dil algılama ---
def detect_language(text):
    prediction = fasttext_model.predict(text)[0][0]
    return prediction.replace("__label__", "")

# --- İngilizce -> Türkçe çeviri ---
def translate_text(text):
    inputs = tokenizer([text], return_tensors="pt", padding=True, truncation=True)
    translated = translator.generate(**inputs)
    return tokenizer.decode(translated[0], skip_special_tokens=True)

# --- Türkçe sesli çıktı ---
def speak_text(text):
    tts.tts_to_file(text=text, file_path="/root/novadc/out.wav")
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "/root/novadc/out.wav"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# --- Ana işlem ---
def run():
    stream = stream_audio(url)
    recognizer = KaldiRecognizer(vosk_en, 16000)
    buffer = b""

    try:
        while True:
            data = stream.stdout.read(4000)
            if not data:
                break

            buffer += data
            if len(buffer) < 8000:
                continue

            if recognizer.AcceptWaveform(buffer):
                result = json.loads(recognizer.Result())
                text = result.get("text", "")
                buffer = b""

                if not text.strip():
                    continue

                print(f"[Tespit]: {text}")
                lang = detect_language(text)
                print(f"[Dil]: {lang}")

                if lang == "en":
                    tr_text = translate_text(text)
                    print(f"[Türkçe]: {tr_text}")
                    speak_text(tr_text)
                elif lang == "tr":
                    speak_text(text)
                else:
                    print(f"[!] Desteklenmeyen dil: {lang}")
    except KeyboardInterrupt:
        print(">> Yayın durduruldu (Ctrl+C).")
    except Exception as e:
        print(">> Hata:", str(e))

# --- Başlat ---
if __name__ == "__main__":
    run()
