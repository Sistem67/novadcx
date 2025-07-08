import subprocess
import vosk
import json
import asyncio
import websockets
import os
from googletrans import Translator
import fasttext

# --- Ayarlar ---
YAYIN_LINKI = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"  # Örnek m3u8 yayını
VOSK_MODEL_PATH = "model"  # Örn: "model-en" veya "model-tr"
FASTTEXT_MODEL = "lid.176.bin"
PORT = 8000

# --- Modelleri yükle ---
model = vosk.Model(VOSK_MODEL_PATH)
lang_model = fasttext.load_model(FASTTEXT_MODEL)
translator = Translator()

# --- WebSocket istemcileri ---
clients = set()

async def websocket_handler(websocket, path):
    clients.add(websocket)
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        clients.remove(websocket)

async def send_to_clients(message):
    for client in clients.copy():
        try:
            await client.send(message)
        except:
            clients.remove(client)

async def stream_audio():
    process = subprocess.Popen([
        "ffmpeg", "-loglevel", "quiet", "-i", YAYIN_LINKI,
        "-f", "s16le", "-ac", "1", "-ar", "16000", "-"
    ], stdout=subprocess.PIPE)

    recognizer = vosk.KaldiRecognizer(model, 16000)

    while True:
        data = process.stdout.read(4000)
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get("text", "")
            if text:
                lang = lang_model.predict(text)[0][0].replace("__label__", "")
                if lang != "tr":
                    translated = translator.translate(text, src=lang, dest="tr").text
                else:
                    translated = text

                print(f"[{lang}] → {translated}")
                await send_to_clients(translated)

                # Dosyaya yaz
                with open("translated.txt", "w", encoding="utf-8") as f:
                    f.write(translated)

async def main():
    print(f"WebSocket başlatıldı: ws://0.0.0.0:{PORT}")
    server = await websockets.serve(websocket_handler, "0.0.0.0", PORT)
    await stream_audio()

if __name__ == "__main__":
    asyncio.run(main())
