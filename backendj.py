import argparse import asyncio import subprocess import json import os import sys from vosk import Model, KaldiRecognizer from googletrans import Translator import websockets

Model yolu sabit

MODEL_PATH = "/root/vosk-model-small-tr-0.3"

WebSocket bağlantı seti

clients = set()

Altyazı tekrarlarını engellemek için önceki metin

previous_text = ""

WebSocket'e altyazı gönder

async def broadcast(text): if clients and text: data = json.dumps({"text": text}, ensure_ascii=False) await asyncio.wait([client.send(data) for client in clients])

Yayından sesi al, altyazıya çevir, çeviriyi gönder

async def recognize_and_translate(stream_url): if not os.path.exists(MODEL_PATH): print("Model klasörü bulunamadı:", MODEL_PATH) sys.exit(1)

model = Model(MODEL_PATH)
recognizer = KaldiRecognizer(model, 16000)
recognizer.SetWords(True)

translator = Translator()

print("Yayından ses alınıyor...")
process = subprocess.Popen([
    "ffmpeg", "-i", stream_url,
    "-ar", "16000", "-ac", "1",
    "-f", "s16le", "-loglevel", "quiet", "-"
], stdout=subprocess.PIPE)

global previous_text

while True:
    data = process.stdout.read(4000)
    if len(data) == 0:
        continue

    if recognizer.AcceptWaveform(data):
        result = json.loads(recognizer.Result())
        text = result.get("text", "").strip()
        if text and text != previous_text:
            previous_text = text
            try:
                translated = translator.translate(text, dest="tr").text
                print(translated)
                await broadcast(translated)
            except Exception as e:
                print("Çeviri hatası:", e)
                print(text)
                await broadcast(text)
    else:
        partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
        if partial and partial != previous_text:
            previous_text = partial
            print(partial, end="\r")

WebSocket bağlantı yöneticisi

async def websocket_handler(websocket): clients.add(websocket) try: await websocket.wait_closed() finally: clients.remove(websocket)

Ana program

async def main(url): print("WebSocket sunucusu çalışıyor: ws://0.0.0.0:8000") asyncio.create_task(recognize_and_translate(url)) async with websockets.serve(websocket_handler, "0.0.0.0", 8000): await asyncio.Future()

if name == "main": parser = argparse.ArgumentParser() parser.add_argument("--url", required=True, help="Canlı yayın URL (m3u8)") args = parser.parse_args()

try:
    asyncio.run(main(args.url))
except KeyboardInterrupt:
    print("Program durduruldu.")

