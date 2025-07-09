import asyncio
import subprocess
import websockets
import numpy as np
import whisper
import fasttext
from googletrans import Translator

import json

clients = set()
print("Model yükleniyor: Whisper...")
model = whisper.load_model("base")
print("Model yükleniyor: FastText...")
lang_model = fasttext.load_model("lid.176.bin")
translator = Translator()

async def broadcast(message):
    if clients:
        data = json.dumps(message, ensure_ascii=False)
        await asyncio.wait([client.send(data) for client in clients])

def pcm_bytes_to_float32(audio_bytes):
    import struct
    int16 = np.frombuffer(audio_bytes, np.int16)
    float32 = int16.astype(np.float32) / 32768.0
    return float32

async def transcribe_stream(m3u8_url):
    print(f"Yayından ses alınıyor: {m3u8_url}")

    # ffmpeg: pcm_s16le, 16000Hz, mono
    process = subprocess.Popen([
        "ffmpeg", "-i", m3u8_url,
        "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", "-"
    ], stdout=subprocess.PIPE)

    buffer = b""
    chunk_size = 16000 * 2 * 5  # 5 saniye, 16kHz * 2 byte (int16) * 5

    while True:
        data = process.stdout.read(chunk_size)
        if not data:
            continue

        # Ses verisini float32 numpy dizisine dönüştür
        audio_float32 = pcm_bytes_to_float32(data)

        # Whisper ile transkripsiyon (raw audio array input)
        result = model.transcribe(audio_float32, fp16=False, language=None)  # dil otomatik algılanır
        text = result["text"].strip()

        if not text:
            continue

        lang_detected = lang_model.predict(text)[0][0].replace("__label__", "")
        print(f"[{lang_detected}] {text}")

        if lang_detected != "tr":
            try:
                translated = translator.translate(text, src=lang_detected, dest="tr").text
                print(f"[TR] {translated}")
                await broadcast({"text": translated})
            except Exception as e:
                print(f"Çeviri hatası: {e}")
        else:
            await broadcast({"text": text})

async def socket_handler(websocket):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def main(url):
    print("WebSocket sunucusu çalışıyor: ws://0.0.0.0:8000")
    asyncio.create_task(transcribe_stream(url))
    async with websockets.serve(socket_handler, "0.0.0.0", 8000):
        await asyncio.Future()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Canlı yayın m3u8 URL'si")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Program durduruldu.")
