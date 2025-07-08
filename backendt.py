import argparse
import asyncio
import subprocess
import json
import fasttext
from vosk import Model, KaldiRecognizer
from transformers import MarianMTModel, MarianTokenizer
from TTS.api import TTS
import websockets

# Argümanları al
parser = argparse.ArgumentParser(description="WebSocket destekli canlı çeviri backend")
parser.add_argument("--url", type=str, required=True, help="Yayın linki")
parser.add_argument("--host", type=str, default="0.0.0.0", help="WebSocket host (default: 0.0.0.0)")
parser.add_argument("--port", type=int, default=8765, help="WebSocket port (default: 8765)")
args = parser.parse_args()

# Model yolları
FASTTEXT_MODEL_PATH = "lid.176.bin"  # .bin uzantılı model
VOSK_MODELS = {
    "en": "models/vosk-model-small-en-us-0.15",
    "tr": "models/vosk-model-small-tr-0.3"
}
MARIAN_MODEL_NAME = "Helsinki-NLP/opus-mt-en-tr"
TTS_MODEL_NAME = "tts_models/tr/mai/tacotron2-DDC"

print("[INFO] Modeller yükleniyor...")
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
vosk_en = Model(VOSK_MODELS["en"])
vosk_tr = Model(VOSK_MODELS["tr"])
tokenizer = MarianTokenizer.from_pretrained(MARIAN_MODEL_NAME)
translator = MarianMTModel.from_pretrained(MARIAN_MODEL_NAME)
tts = TTS(model_name=TTS_MODEL_NAME, progress_bar=False, gpu=False)
print("[INFO] Modeller yüklendi.")

def ffmpeg_command(url):
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

async def tts_to_bytes(text):
    wav_path = "temp_output.wav"
    tts.tts_to_file(text=text, file_path=wav_path)
    with open(wav_path, "rb") as f:
        data = f.read()
    return data

async def process_audio(websocket, path):
    print("[INFO] Yeni istemci bağlandı:", websocket.remote_address)
    process = subprocess.Popen(ffmpeg_command(args.url), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    buffer = b""
    lang = None
    recognizer = None

    try:
        while True:
            audio_chunk = process.stdout.read(4000)
            if not audio_chunk:
                break
            buffer += audio_chunk

            if not recognizer and len(buffer) > 16000 * 3:
                temp_rec = KaldiRecognizer(vosk_en, 16000)
                if temp_rec.AcceptWaveform(buffer):
                    sample_text = json.loads(temp_rec.Result()).get("text", "")
                    lang, conf = detect_language(sample_text)
                    print(f"[LANG] Algılanan dil: {lang} (Güven: {conf:.2f})")
                    if lang == "en":
                        recognizer = KaldiRecognizer(vosk_en, 16000)
                    elif lang == "tr":
                        recognizer = KaldiRecognizer(vosk_tr, 16000)
                    else:
                        print("[WARN] Desteklenmeyen dil:", lang)
                        await websocket.send(json.dumps({"error": "Unsupported language"}))
                        break
                    recognizer.SetWords(True)
                    buffer = b""

            elif recognizer:
                if recognizer.AcceptWaveform(audio_chunk):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").strip()
                    if text:
                        print("[ORIGINAL]:", text)
                        if lang == "en":
                            translated = translate(text)
                            print("[TRANSLATED]:", translated)
                            # Mesajı json olarak gönder
                            await websocket.send(json.dumps({
                                "original": text,
                                "translated": translated,
                            }))
                            # Ses verisini byte olarak gönder (isteğe bağlı)
                            tts_data = await tts_to_bytes(translated)
                            await websocket.send(tts_data)
                        elif lang == "tr":
                            await websocket.send(json.dumps({"original": text}))

    except Exception as e:
        print("[ERROR]", e)
    finally:
        process.kill()
        print("[INFO] İstemci bağlantısı sonlandı.")

async def main():
    async with websockets.serve(process_audio, args.host, args.port):
        print(f"[INFO] WebSocket sunucusu başlatıldı ws://{args.host}:{args.port}")
        await asyncio.Future()  # sonsuz döngü

if __name__ == "__main__":
    asyncio.run(main())
