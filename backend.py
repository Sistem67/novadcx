import argparse
import subprocess
import json
import asyncio
import websockets
from vosk import Model, KaldiRecognizer
from langdetect import detect
from googletrans import Translator
import os

# Ayarlar
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000

clients = set()

def load_model(language_code):
    model_paths = {
        'en': 'vosk-model-small-en-us-0.15',
        'tr': 'vosk-model-small-tr-0.3'
    }
    path = model_paths.get(language_code, model_paths['en'])
    if not os.path.exists(path):
        raise Exception(f"Model not found: {path}")
    return Model(path)

async def subtitle_server(websocket, path):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def send_subtitle(text):
    if clients:
        await asyncio.wait([client.send(text) for client in clients])

def stream_audio(url):
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

async def run(url):
    translator = Translator()
    recognizer = KaldiRecognizer(load_model('en'), SAMPLE_RATE)
    recognizer.SetWords(True)
    audio_stream = stream_audio(url)

    print("Listening to the stream...")

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get('text', '').strip()

            if text:
                try:
                    detected_lang = detect(text)
                    print(f"Detected language: {detected_lang} | Text: {text}")

                    if detected_lang == 'tr':
                        translated = translator.translate(text, dest='en').text
                    else:
                        translated = translator.translate(text, dest='tr').text

                    print(f"Subtitle: {translated}")
                    await send_subtitle(translated)

                except Exception as e:
                    print(f"Error: {e}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True, help='M3U8 stream URL')
    args = parser.parse_args()

    start_server = websockets.serve(subtitle_server, '0.0.0.0', 8000)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_server)
    loop.run_until_complete(run(args.url))
    loop.run_forever()
