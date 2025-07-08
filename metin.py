import ffmpeg
import subprocess
import vosk
import json
import websockets
import asyncio
import os
from googletrans import Translator
import fasttext

# Model yükle
model = vosk.Model("model")  # İngilizce ya da Türkçe modeli
lang_model = fasttext.load_model("lid.176.bin")
translator = Translator()

# WebSocket sunucusu
clients = set()

async def websocket_handler(websocket, path):
    clients.add(websocket)
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        clients.remove(websocket)

async def stream_loop():
    process = subprocess.Popen([
        "ffmpeg", "-loglevel", "quiet", "-i", "YAYIN_LINKIN", "-f", "s16le",
        "-ac", "1", "-ar", "16000", "-"
    ], stdout=subprocess.PIPE)

    rec = vosk.KaldiRecognizer(model, 16000)

    while True:
        data = process.stdout.read(4000)
        if not data:
            break

        if rec.AcceptWaveform(data):
            result = json.loads(rec.Result())
            text = result.get("text", "")
            if text:
                lang = lang_model.predict(text)[0][0].replace("__label__", "")
                if lang != "tr":
                    translation = translator.translate(text, src=lang, dest="tr").text
                    await send_to_clients(translation)
                    with open("translated.txt", "w", encoding="utf-8") as f:
                        f.write(translation)
                else:
                    await send_to_clients(text)
                    with open("translated.txt", "w", encoding="utf-8") as f:
                        f.write(text)

async def send_to_clients(message):
    for client in clients:
        await client.send(message)

# WebSocket başlat
start_server = websockets.serve(websocket_handler, "0.0.0.0", 8000)
loop = asyncio.get_event_loop()
loop.run_until_complete(start_server)
loop.create_task(stream_loop())
loop.run_forever()
