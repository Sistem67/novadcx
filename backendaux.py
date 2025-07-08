import argparse
import asyncio
import subprocess
import json
import os
import fasttext
from vosk import Model, KaldiRecognizer
from transformers import MarianMTModel, MarianTokenizer
from TTS.api import TTS
import websockets

# --- Argümanları al ---
parser = argparse.ArgumentParser(description="WebSocket destekli canlı çeviri backend")
parser.add_argument("--url", type=str, required=True, help="Yayın linki")
parser.add_argument("--host", type=str, default="0.0.0.0", help="WebSocket host (default: 0.0.0.0)")
parser.add_argument("--port", type=int, default=8765, help="WebSocket port (default: 8765)")
args = parser.parse_args()

# --- Model Yolları ---
FASTTEXT_MODEL_PATH = "lid.176.bin"  # FastText .bin modeli
VOSK_MODEL_PATHS = {
    "en": "models/vosk-model-small-en-us-0.15",
    "tr": "models/vosk-model-small-tr-0.3"
}
MARIAN_MODEL_NAME = "Helsinki-NLP/opus-mt-en-tr"
TTS_MODEL_NAME = "tts_models/tr/mai/tacotron2-DDC"

# --- Modelleri yükle ---
print("[INFO] FastText modeli yükleniyor...")
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)

def load_vosk_model(path):
    if os.path.exists(path):
        print(f"[INFO] Vosk modeli yüklendi: {path}")
        return Model(path)
    else:
        print(f"[WARN] Vosk modeli bulunamadı: {path}")
        return None

vosk_en = load_vosk_model(VOSK_MODEL_PATHS["en"])
vosk_tr = load_vosk_model(VOSK_MODEL_PATHS["tr"])

print("[INFO] MarianMT modeli yükleniyor...")
tokenizer = MarianTokenizer.from_pretrained(MARIAN_MODEL_NAME)
translator = MarianMTModel.from_pretrained(MARIAN_MODEL_NAME)

print("[INFO] Coqui TTS modeli yükleniyor...")
tts = TTS(model_name=TTS_MODEL_NAME, progress_bar=False, gpu=False)

print("[INFO] Tüm modeller yüklendi.\n")

# --- ffmpeg komutu ---
def ffmpeg_cmd(url):
    return [
        "ffmpeg", "-i", url,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        "-f", "wav", "pipe:1"
    ]

# --- Dil algılama ---
def detect_language(text):
    labels, scores = fasttext_model.predict(text)
    lang = labels[0].replace("__label__", "")
    conf = scores[0]
    return lang, conf

# --- Çeviri ---
def translate(text):
    inputs = tokenizer(text, return_tensors="pt", padding=True)
    outputs = translator.generate(**inputs)
    return tokenizer.decode(outputs[0], skip_special_tokens=True)

# --- TTS ---
def tts_to_bytes(text):
    temp_wav = "temp_output.wav"
    tts.tts_to_file(text=text, file_path=temp_wav)
    with open(temp_wav, "rb") as f:
        data = f.read()
    os.remove(temp_wav)
    return data

# --- Vosk recognizer seç ---
def get_recognizer(lang, sample_rate=16000):
    if lang == "en" and vosk_en:
        return KaldiRecognizer(vosk_en, sample_rate)
    elif lang == "tr" and vosk_tr:
        return KaldiRecognizer(vosk_tr, sample_rate)
    else:
        print(f"[ERROR] Desteklenmeyen dil veya model yok: {lang}")
        return None

# --- WebSocket ile yayını işle ---
async def handler(websocket, path):
    print(f"[INFO] Yeni istemci bağlandı: {websocket.remote_address}")

    process = subprocess.Popen(ffmpeg_cmd(args.url), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buffer = b""
    lang = None
    recognizer = None

    try:
        while True:
            audio_chunk = process.stdout.read(4000)
            if not audio_chunk:
                break
            buffer += audio_chunk

            # İlk 3 saniyede dil algıla
            if not recognizer and len(buffer) > 16000 * 3:
                temp_rec = KaldiRecognizer(vosk_en if vosk_en else vosk_tr, 16000)
                if temp_rec.AcceptWaveform(buffer):
                    sample_text = json.loads(temp_rec.Result()).get("text", "")
                    lang, conf = detect_language(sample_text)
                    print(f"[LANG] Algılanan dil: {lang} (Güven: {conf:.2f})")

                    recognizer = get_recognizer(lang)
                    if not recognizer:
                        await websocket.send(json.dumps({"error": "Unsupported language or missing model"}))
                        break

                    recognizer.SetWords(True)
                    buffer = b""

            elif recognizer:
                if recognizer.AcceptWaveform(audio_chunk):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").strip()
                    if text:
                        print(f"[ORIGINAL]: {text}")
                        if lang == "en":
                            translated = translate(text)
                            print(f"[TRANSLATED]: {translated}")
                            await websocket.send(json.dumps({"original": text, "translated": translated}))
                            tts_data = tts_to_bytes(translated)
                            await websocket.send(tts_data)
                        elif lang == "tr":
                            await websocket.send(json.dumps({"original": text}))

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        process.kill()
        print("[INFO] İstemci bağlantısı kapandı.")

async def main():
    async with websockets.serve(handler, args.host, args.port):
        print(f"[INFO] WebSocket sunucusu çalışıyor: ws://{args.host}:{args.port}")
        await asyncio.Future()  # sonsuz bekleme

if __name__ == "__main__":
    asyncio.run(main())
