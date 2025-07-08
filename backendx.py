import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
from googletrans import Translator
from langdetect import detect
import os

# Sabitler
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000

clients = set()

# Vosk modeli yolu (Ubuntu’da root ana dizinde olduğunu varsayıyoruz)
model_path = "/root/vosk-model-small-tr-0.3"
if not os.path.exists(model_path):
    raise FileNotFoundError(f"Model dizini bulunamadı: {model_path}")

model = Model(model_path)
translator = Translator()

async def subtitle_server(websocket, path):
    clients.add(websocket)
    print(f"Yeni bağlanan istemci: {websocket.remote_address}")
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)
        print(f"İstemci ayrıldı: {websocket.remote_address}")

async def send_subtitle(text):
    if clients:
        await asyncio.wait([client.send(text) for client in clients])

def stream_audio(url):
    # ffmpeg ile yayından 16kHz mono PCM ses çıkarır
    process = subprocess.Popen([
        'ffmpeg',
        '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le',
        '-'],
        stdout=subprocess.PIPE
    )
    return process.stdout

async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            print("Ses verisi gelmiyor, döngü sonlandırılıyor.")
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get('text', '').strip()

            if text:
                try:
                    detected_lang = detect(text)
                    print(f"Tespit edilen dil: {detected_lang} | Metin: {text}")

                    # Eğer dil Türkçe ise İngilizce'ye çevir, değilse Türkçe'ye çevir
                    if detected_lang == 'tr':
                        translated = translator.translate(text, dest='en').text
                    else:
                        translated = translator.translate(text, dest='tr').text

                    print(f"Altyazı (çeviri): {translated}")
                    await send_subtitle(translated)

                except Exception as e:
                    print(f"Çeviri hatası: {e}")

async def main(url):
    # Websocket sunucusunu başlat
    start_server = websockets.serve(subtitle_server, '0.0.0.0', 8000)
    await start_server
    print("Websocket server 8000 portunda başlatıldı.")
    await recognize_and_translate(url)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Canlı yayından otomatik altyazı çıkarma ve çeviri")
    parser.add_argument('--url', required=True, help='M3U8 canlı yayın URLsi')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Program sonlandırıldı.")
