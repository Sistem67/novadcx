import argparse import asyncio import subprocess import tempfile import json import websockets import whisper import fasttext from googletrans import Translator

Modelleri yükle

whisper_model = whisper.load_model("base") lang_model = fasttext.load_model("lid.176.bin") translator = Translator()

clients = set()

async def transcribe_stream(m3u8_url): print("Canlı yayın dinleniyor:", m3u8_url)

process = subprocess.Popen([
    "ffmpeg", "-i", m3u8_url,
    "-loglevel", "quiet",
    "-f", "wav", "-ac", "1", "-ar", "16000", "-"
], stdout=subprocess.PIPE)

with tempfile.NamedTemporaryFile(suffix=".wav") as tmpfile:
    while True:
        audio_chunk = process.stdout.read(16000 * 2 * 5)  # 5 saniye WAV
        if not audio_chunk:
            continue

        tmpfile.seek(0)
        tmpfile.write(audio_chunk)
        tmpfile.flush()

        try:
            result = whisper_model.transcribe(tmpfile.name, fp16=False)
            text = result["text"].strip()
            if not text:
                continue

            lang_detected = lang_model.predict(text)[0][0].replace("__label__", "")
            print(f"[{lang_detected}] {text}")

            if lang_detected != "tr":
                try:
                    translated = translator.translate(text, src=lang_detected, dest="tr").text
                    print("[TR]", translated)
                    await broadcast({"text": translated})
                except Exception as e:
                    print("Çeviri hatası:", e)
            else:
                await broadcast({"text": text})

        except Exception as err:
            print("Hata:", err)

async def broadcast(message): if clients: data = json.dumps(message, ensure_ascii=False) await asyncio.wait([client.send(data) for client in clients])

async def socket_handler(websocket): clients.add(websocket) try: await websocket.wait_closed() finally: clients.remove(websocket)

async def main(url): print("WebSocket sunucusu çalışıyor: ws://0.0.0.0:8000") asyncio.create_task(transcribe_stream(url)) async with websockets.serve(socket_handler, "0.0.0.0", 8000): await asyncio.Future()

if name == "main": parser = argparse.ArgumentParser() parser.add_argument("--url", required=True, help="Yayın URL'si (.m3u8)") args = parser.parse_args()

try:
    asyncio.run(main(args.url))
except KeyboardInterrupt:
    print("Durduruldu.")

