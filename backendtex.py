import subprocess
import vosk
import json
import asyncio
import websockets
import os
from googletrans import Translator
import fasttext

# VOSK ve FastText modellerini yükle
model = vosk.Model("model")  # Türkçe veya İngilizce modelin yüklü olduğu klasör
lang_model = fasttext.load_model("lid.176.bin")
translator = Translator()

# WebSocket istemcileri
clients = set()

async def websocket_handler(websocket, path):
    clients.add(websocket)
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        clients.remove(websocket)

async def send_to_clients(message):
    for client in clients:
        try:
            await client.send(message)
        except:
            pass

async def stream_audio():
    process = subprocess.Popen([
        "ffmpeg", "-loglevel", "quiet", "-i", "YAYIN_LINKIN", "-f", "s16le", "-ac", "1", "-ar", "16000", "-"
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
                await send_to_clients(translated)
                with open("translated.txt", "w", encoding="utf-8") as f:
                    f.write(translated)

async def main():
    server = websockets.serve(websocket_handler, "0.0.0.0", 8000)
    await server
    await stream_audio()

asyncio.run(main())
