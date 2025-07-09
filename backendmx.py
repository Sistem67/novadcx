import argparse
import asyncio
import subprocess
import tempfile
import json
import datetime
import websockets
import whisper
import fasttext
from googletrans import Translator

# Modelleri yükle (yüklenme süresi olabilir)
print("Model yükleniyor: Whisper...")
whisper_model = whisper.load_model("large-v3")  # En yüksek kalite, GPU varsa kullan
print("Model yükleniyor: FastText...")
lang_model = fasttext.load_model("lid.176.bin")
translator = Translator()

clients = set()
subtitle_index = 1

def format_timestamp(seconds: float) -> str:
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

    # ffmpeg ile ses akışını 16kHz mono WAV olarak al
    process = subprocess.Popen([
        "ffmpeg", "-i", m3u8_url,
        "-loglevel", "quiet",
        "-f", "wav", "-ac", "1", "-ar", "16000", "-"
    ], stdout=subprocess.PIPE)

    with tempfile.NamedTemporaryFile(suffix=".wav") as tmpfile, open("output.srt", "w", encoding="utf-8") as srt_file:
        while True:
            audio_chunk = process.stdout.read(16000 * 2 * 5)  # 5 saniyelik ses (16kHz*2byte*5s)
            if not audio_chunk:
                continue

            tmpfile.seek(0)
            tmpfile.write(audio_chunk)
            tmpfile.flush()

            try:
                # Whisper transkripsiyon
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

                    # Dil algılama
                    lang_detected = lang_model.predict(text)[0][0].replace("__label__", "")
                    print(f"[{lang_detected}] {text}")

                    # Çeviri (dil Türkçe değilse)
                    if lang_detected != "tr":
                        try:
                            translated = translator.translate(text, src=lang_detected, dest="tr").text
                            print(f"[TR] {translated}")
                            display_text = translated
                        except Exception as e:
                            print(f"Çeviri hatası: {e}")
                            display_text = text
                    else:
                        display_text = text

                    # SRT formatında yaz
                    srt_file.write(f"{subtitle_index}\n")
                    srt_file.write(f"{format_timestamp(start)} --> {format_timestamp(end)}\n")
                    srt_file.write(f"{display_text}\n\n")
                    srt_file.flush()

                    subtitle_index += 1

                    # WebSocket ile gerçek zamanlı gönder
                    await broadcast({
                        "text": display_text,
                        "start": start,
                        "end": end,
                        "lang": lang_detected
                    })

            except Exception as err:
                print(f"Hata: {err}")

async def broadcast(message):
    if clients:
        data = json.dumps(message, ensure_ascii=False)
        await asyncio.wait([client.send(data) for client in clients])

async def socket_handler(websocket):
    clients.add(websocket)
    print(f"Yeni istemci bağlandı: {websocket.remote_address}")
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)
        print(f"İstemci bağlantısı kesildi: {websocket.remote_address}")

async def main(url):
    print("WebSocket sunucusu başlatıldı: ws://0.0.0.0:8000")
    asyncio.create_task(transcribe_stream(url))
    async with websockets.serve(socket_handler, "0.0.0.0", 8000):
        await asyncio.Future()  # Sonsuz bekleme

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Profesyonel Canlı Altyazı Backend")
    parser.add_argument("--url", required=True, help="Canlı yayın m3u8 URL'si")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Program durduruldu.")
