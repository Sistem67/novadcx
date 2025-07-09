import argparse
import asyncio
import subprocess
import tempfile
import json
import websockets
import whisper
import fasttext
from googletrans import Translator
import datetime

# Modelleri yükle
whisper_model = whisper.load_model("base")  # medium, large seçenekleri var
lang_model = fasttext.load_model("lid.176.bin")
translator = Translator()

clients = set()
subtitle_index = 1

def format_timestamp(seconds: float) -> str:
    """SRT formatı için zaman damgası oluşturur"""
    td = datetime.timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    milliseconds = int((td.total_seconds() - total_seconds) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"

async def transcribe_stream(m3u8_url):
    global subtitle_index
    print(f"Yayından ses alınıyor: {m3u8_url}")

    process = subprocess.Popen([
        "ffmpeg", "-i", m3u8_url,
        "-loglevel", "quiet",
        "-f", "wav", "-ac", "1", "-ar", "16000", "-"
    ], stdout=subprocess.PIPE)

    with tempfile.NamedTemporaryFile(suffix=".wav") as tmpfile, open("output.srt", "w", encoding="utf-8") as srt_file:
        while True:
            audio_chunk = process.stdout.read(16000 * 2 * 5)  # 5 saniye
            if not audio_chunk:
                continue

            tmpfile.seek(0)
            tmpfile.write(audio_chunk)
            tmpfile.flush()

            try:
                result = whisper_model.transcribe(tmpfile.name, fp16=False)
                segments = result.get("segments", [])
                if not segments:
                    continue

                for segment in segments:
                    start = segment["start"]
                    end = segment["end"]
                    text = segment["text"].strip()

                    if not text:
                        continue

                    lang_detected = lang_model.predict(text)[0][0].replace("__label__", "")
                    print(f"[{lang_detected}] {text}")

                    if lang_detected != "tr":
                        try:
                            translated = translator.translate(text, src=lang_detected, dest="tr").text
                            print("[TR]", translated)
                            text = translated
                        except Exception as e:
                            print("Çeviri hatası:", e)
                            continue

                    # SRT formatında yaz
                    srt_file.write(f"{subtitle_index}\n")
                    srt_file.write(f"{format_timestamp(start)} --> {format_timestamp(end)}\n")
                    srt_file.write(f"{text}\n\n")
                    srt_file.flush()

                    subtitle_index += 1

                    await broadcast({"text": text, "start": start, "end": end})

            except Exception as err:
                print("Hata:", err)

async def broadcast(message):
    if clients:
        data = json.dumps(message, ensure_ascii=False)
        await asyncio.wait([client.send(data) for client in clients])

async def socket_handler(websocket):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def main(url):
    print("WebSocket sunucusu başlatıldı: ws://0.0.0.0:8000")
    asyncio.create_task(transcribe_stream(url))
    async with websockets.serve(socket_handler, "0.0.0.0", 8000):
        await asyncio.Future()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Yayın URL'si (m3u8)")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Yayın durduruldu.")
