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
VOSK_MODEL_PATH = "/root/vosk-model-small-tr-0.3"

if not os.path.exists(VOSK_MODEL_PATH):
    raise FileNotFoundError(f"Vosk modeli bulunamadı: {VOSK_MODEL_PATH}")

model = Model(VOSK_MODEL_PATH)
translator = Translator()
clients = set()
tts_queue = Queue()

def tts_worker():
    while True:
        text, lang = tts_queue.get()
        try:
            with tempfile.NamedTemporaryFile(delete=True, suffix=".mp3") as fp:
                tts = gTTS(text=text, lang=lang)
                tts.save(fp.name)
                os.system(f"mpg123 -q {fp.name}")
        except Exception as e:
            print(f"TTS Hatası: {e}")
        tts_queue.task_done()

threading.Thread(target=tts_worker, daemon=True).start()

def queue_tts(text, lang='tr'):
    tts_queue.put((text, lang))

async def subtitle_server(websocket, path):
    clients.add(websocket)
    print(f"WebSocket bağlandı: {websocket.remote_address}")
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)
        print(f"WebSocket ayrıldı: {websocket.remote_address}")

async def send_subtitle(text):
    if clients:
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
            print("Ses verisi yok. Yayın bitmiş olabilir.")
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get('text', '').strip()

            if text:
                buffered_text += " " + text

                if len(buffered_text) > 30:
                    try:
                        # Yayın dili sabit: İngilizce
                        detected_lang = 'en'

                        translated = translator.translate(buffered_text, dest='tr').text
                        tts_lang = 'tr'

                        print(f"Çeviri (TR): {translated}")
                        await send_subtitle(translated)
                        queue_tts(translated, tts_lang)
                        buffered_text = ""

                    except Exception as e:
                        print(f"Hata: {e}")

async def main(url):
    server = websockets.serve(subtitle_server, "0.0.0.0", 8000)
    await server
    print("WebSocket sunucusu 8000 portunda hazır.")
    await recognize_and_translate(url)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Canlı yayın altyazı ve sesli çeviri")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Program durduruldu.")
