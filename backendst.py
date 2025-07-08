import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
from googletrans import Translator
import os
from gtts import gTTS
import tempfile
from queue import Queue
import threading

SAMPLE_RATE = 16000
CHUNK_SIZE = 4000

VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"
FORCE_INPUT_LANG = 'en'
FORCE_OUTPUT_LANG = 'tr'

model = Model(VOSK_MODEL_PATH)
translator = Translator()
clients = set()
tts_queue = Queue()

def tts_worker():
    while True:
        text, lang = tts_queue.get()
        if len(text) > 15:
            try:
                with tempfile.NamedTemporaryFile(delete=True, suffix=".mp3") as fp:
                    tts = gTTS(text=text, lang=lang)
                    tts.save(fp.name)
                    os.system(f"mpg123 -q {fp.name}")
            except Exception as e:
                print("TTS Hatası:", e)
        tts_queue.task_done()

threading.Thread(target=tts_worker, daemon=True).start()

def queue_tts(text, lang='tr'):
    if text.strip() and len(text) > 15 and not text.isnumeric():
        tts_queue.put((text, lang))

async def subtitle_server(websocket, path):
    clients.add(websocket)
    print("WebSocket bağlandı:", websocket.remote_address)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)
        print("WebSocket ayrıldı:", websocket.remote_address)

async def send_subtitle(text):
    if clients and text.strip():
        await asyncio.wait([client.send(text) for client in clients])

def stream_audio(url):
    return subprocess.Popen([
        'ffmpeg',
        '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le',
        '-'
    ], stdout=subprocess.PIPE).stdout

async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    buffered_text = ""

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            print("Ses verisi yok.")
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get('text', '').strip()

            if text:
                buffered_text += " " + text

                if len(buffered_text) >= 40:
                    try:
                        translated = translator.translate(
                            buffered_text,
                            src=FORCE_INPUT_LANG,
                            dest=FORCE_OUTPUT_LANG
                        ).text

                        await send_subtitle(translated)
                        queue_tts(translated, FORCE_OUTPUT_LANG)
                        buffered_text = ""

                    except Exception as e:
                        print("Çeviri hatası:", e)
                        buffered_text = ""

async def main(url):
    server = websockets.serve(subtitle_server, "0.0.0.0", 8000)
    await server
    print("WebSocket sunucusu başlatıldı.")
    await recognize_and_translate(url)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Yayın çeviri sistemi")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Program durduruldu.")
