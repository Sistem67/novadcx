import argparse import subprocess import wave import json import asyncio import websockets from vosk import Model, KaldiRecognizer from langdetect import detect from googletrans import Translator import os

-------------------------------

🔥 Ayarlar

-------------------------------

SAMPLE_RATE = 16000 CHUNK_SIZE = 4000

-------------------------------

🌐 WebSocket client listesi

-------------------------------

clients = set()

-------------------------------

🔄 Dil modeli yükleyici

-------------------------------

def load_model(language_code): model_paths = { 'en': 'vosk-model-small-en-us-0.15', 'tr': 'vosk-model-small-tr-0.22' } path = model_paths.get(language_code, model_paths['en']) if not os.path.exists(path): raise Exception(f"Model bulunamadı: {path}") return Model(path)

-------------------------------

🔁 WebSocket server

-------------------------------

async def subtitle_server(websocket, path): clients.add(websocket) try: await websocket.wait_closed() finally: clients.remove(websocket)

-------------------------------

📡 Altyazıyı herkese gönder

-------------------------------

async def send_subtitle(text): if clients: await asyncio.wait([client.send(text) for client in clients])

-------------------------------

🔊 ffmpeg ile yayından ses al

-------------------------------

def stream_audio(url): process = subprocess.Popen([ 'ffmpeg', '-i', url, '-loglevel', 'quiet', '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-'], stdout=subprocess.PIPE ) return process.stdout

-------------------------------

🧠 Ana fonksiyon

-------------------------------

async def run(url): translator = Translator() recognizer = KaldiRecognizer(load_model('en'), SAMPLE_RATE) recognizer.SetWords(True) audio_stream = stream_audio(url)

print("🎙️ Yayın dinleniyor...")

while True:
    data = audio_stream.read(CHUNK_SIZE)
    if recognizer.AcceptWaveform(data):
        result = json.loads(recognizer.Result())
        text = result.get('text', '').strip()

        if text:
            try:
                detected_lang = detect(text)
                print(f"📌 Algılanan dil: {detected_lang} | Metin: {text}")

                # Otomatik modele geçiş
                if detected_lang == 'tr':
                    # Türkçe → İngilizce çeviri
                    translated = translator.translate(text, dest='en').text
                else:
                    # Diğer diller → Türkçeye çeviri
                    translated = translator.translate(text, dest='tr').text

                print(f"💬 Altyazı: {translated}")
                await send_subtitle(translated)

            except Exception as e:
                print(f"Hata: {e}")

-------------------------------

🚀 Uygulama başlangıcı

-------------------------------

if name == 'main': parser = argparse.ArgumentParser() parser.add_argument('--url', required=True, help='M3U8 yayın linki') args = parser.parse_args()

start_server = websockets.serve(subtitle_server, '0.0.0.0', 8000)

loop = asyncio.get_event_loop()
loop.run_until_complete(start_server)
loop.run_until_complete(run(args.url))
loop.run_forever()

