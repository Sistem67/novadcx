import argparse
import asyncio
import json
import subprocess
import websockets
from vosk import Model, KaldiRecognizer
from googletrans import Translator
import os

clients = set()

async def broadcast(message):
    if clients:
        data = json.dumps(message, ensure_ascii=False)
        await asyncio.wait([client.send(data) for client in clients])

async def recognize_stream(url, model_path):
    if not os.path.exists(model_path):
        print(f"HATA: Model klasörü bulunamadı: {model_path}")
        return

    print(f"Model yüklüyor: {model_path}")
    model = Model(model_path)
    rec = KaldiRecognizer(model, 16000)
    rec.SetWords(True)

    ffmpeg_cmd = [
        "ffmpeg",
        "-i", url,
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        "-loglevel", "quiet",
        "-"
    ]

    process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE)

    translator = Translator()

    while True:
        data = process.stdout.read(4000)
        if len(data) == 0:
            break

        if rec.AcceptWaveform(data):
            result = json.loads(rec.Result())
            text = result.get("text", "").strip()
            if text:
                print(f"Orijinal: {text}")

                try:
                    tr_text = translator.translate(text, dest="tr").text
                    print(f"Çeviri (TR): {tr_text}")
                    await broadcast({"text": tr_text})
                except Exception as e:
                    print(f"Çeviri hatası: {e}")
                    await broadcast({"text": text})

        else:
            partial = json.loads(rec.PartialResult())
            partial_text = partial.get("partial", "").strip()
            if partial_text:
                print(f"Kısmi: {partial_text}")

async def websocket_handler(websocket):
    clients.add(websocket)
    print(f"Yeni istemci bağlandı: {websocket.remote_address}")
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)
        print(f"İstemci bağlantısı kesildi: {websocket.remote_address}")

async def main(url, model_path):
    print("WebSocket sunucusu başlatıldı: ws://0.0.0.0:8000")
    asyncio.create_task(recognize_stream(url, model_path))
    async with websockets.serve(websocket_handler, "0.0.0.0", 8000):
        await asyncio.Future()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VOSK Canlı Yayın Altyazı Backend")
    parser.add_argument("--url", required=True, help="Canlı yayın m3u8 URL'si")
    parser.add_argument("--model", required=True, help="VOSK model klasörü yolu")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url, args.model))
    except KeyboardInterrupt:
        print("Program durduruldu.")
