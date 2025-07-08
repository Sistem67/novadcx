import asyncio
import subprocess
import json
import os
import fasttext
from vosk import Model, KaldiRecognizer
from transformers import MarianMTModel, MarianTokenizer
from TTS.api import TTS
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# --- AYARLAR ---
VOSK_MODEL_PATHS = {
    "en": "models/vosk-model-small-en-us-0.15",
    "tr": "models/vosk-model-small-tr-0.3"
}
FASTTEXT_MODEL_PATH = "models/lid.176.ftz"
MARIAN_MODEL_NAME = "Helsinki-NLP/opus-mt-en-tr"
TTS_MODEL_NAME = "tts_models/tr/mai/tacotron2-DDC"

# --- FASTAPI BAŞLAT ---
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- MODELLERİ YÜKLE ---
print("Modeller yükleniyor...")
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)

vosk_models = {
    "en": Model(VOSK_MODEL_PATHS["en"]),
    "tr": Model(VOSK_MODEL_PATHS["tr"])
}

tokenizer = MarianTokenizer.from_pretrained(MARIAN_MODEL_NAME)
translator = MarianMTModel.from_pretrained(MARIAN_MODEL_NAME)

tts = TTS(model_name=TTS_MODEL_NAME, progress_bar=False, gpu=False)
print("Tüm modeller yüklendi.")

# --- ffmpeg komutu ---
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

# --- Dil algılama ---
def detect_language(text):
    labels, probs = fasttext_model.predict(text)
    lang = labels[0].replace("__label__", "")
    conf = probs[0]
    return lang, conf

# --- Metin çevirisi ---
def translate_text(text):
    inputs = tokenizer(text, return_tensors="pt", padding=True)
    translated = translator.generate(**inputs)
    return tokenizer.decode(translated[0], skip_special_tokens=True)

# --- TTS üretimi ---
def synthesize_speech(text, path="tts_output.wav"):
    tts.tts_to_file(text=text, file_path=path)

# --- WebSocket yöneticisi ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"[+] WebSocket bağlı: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"[-] Bağlantı koptu. Aktif: {len(self.active_connections)}")

    async def send_json(self, obj: dict):
        for conn in self.active_connections:
            await conn.send_text(json.dumps(obj))

    async def send_audio(self, audio_bytes: bytes):
        for conn in self.active_connections:
            await conn.send_bytes(audio_bytes)

manager = ConnectionManager()

# --- Ses işleme döngüsü ---
async def process_stream(websocket: WebSocket, stream_url: str):
    proc = subprocess.Popen(ffmpeg_command(stream_url), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    buffer = b""
    lang_detected = None
    recognizer = None

    try:
        while True:
            chunk = proc.stdout.read(4000)
            if not chunk:
                break

            buffer += chunk

            if not recognizer:
                # Ses geldikten sonra örnek alınıp dil tespit edilir
                if len(buffer) >= 16000 * 3:  # 3 saniye örnek al
                    temp_model = KaldiRecognizer(vosk_models["en"], 16000)
                    if temp_model.AcceptWaveform(buffer):
                        text = json.loads(temp_model.Result()).get("text", "")
                        lang_detected, conf = detect_language(text)
                        print(f"Algılanan dil: {lang_detected} ({conf:.2f})")
                        if lang_detected in vosk_models:
                            recognizer = KaldiRecognizer(vosk_models[lang_detected], 16000)
                            recognizer.SetWords(True)
                        else:
                            await websocket.send_text(json.dumps({"error": "Desteklenmeyen dil"}))
                            break
            else:
                if recognizer.AcceptWaveform(chunk):
                    result = json.loads(recognizer.Result())
                    original = result.get("text", "").strip()
                    if not original:
                        continue

                    print(f"Orijinal: {original}")
                    if lang_detected == "en":
                        translated = translate_text(original)
                        print(f"Çeviri: {translated}")
                        synthesize_speech(translated)
                        with open("tts_output.wav", "rb") as f:
                            await manager.send_audio(f.read())
                        await manager.send_json({
                            "original": original,
                            "translated": translated,
                            "lang": lang_detected
                        })
                    elif lang_detected == "tr":
                        # Türkçe ise direkt altyazı gönder
                        await manager.send_json({
                            "original": original,
                            "translated": original,
                            "lang": lang_detected
                        })

    except Exception as e:
        print(f"HATA: {e}")
    finally:
        proc.kill()
        await websocket.close()

# --- WebSocket endpoint ---
@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        data = await websocket.receive_text()
        msg = json.loads(data)
        url = msg.get("stream_url")
        if not url:
            await websocket.send_text(json.dumps({"error": "stream_url eksik"}))
            return
        await process_stream(websocket, url)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"Bağlantı hatası: {e}")
        manager.disconnect(websocket)

# --- Ana sayfa ---
@app.get("/")
async def home():
    return HTMLResponse("<h1>Canlı Sesli Çeviri Backend (TR+EN)</h1><p>WebSocket: /ws/stream</p>")
