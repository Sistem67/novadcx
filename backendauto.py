import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
from googletrans import Translator
import fasttext
import os
from gtts import gTTS
import tempfile
from queue import Queue
import threading

SAMPLE_RATE = 16000
CHUNK_SIZE = 4000

VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"  # Ses tanıma modeli
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"  # Dil algılama modeli

if not os.path.exists(VOSK_MODEL_PATH):
    raise FileNotFoundError(f"Vosk modeli bulunamadı: {VOSK_MODEL_PATH}")
if not os.path.exists(FASTTEXT_MODEL_PATH):
    raise FileNotFoundError(f"FastText modeli bulunamadı: {FASTTEXT_MODEL_PATH}")

model = Model(VOSK_MODEL_PATH)
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
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
    print(f"WebSocket bağlandı: {websocket.remote_address}")
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)
        print(f"WebSocket ayrıldı: {websocket.remote_address}")

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
            print("Ses verisi yok, yayın sona ermiş olabilir.")
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get('text', '').strip()

            if text:
                buffered_text += " " + text

                if len(buffered_text) >= 40:
                    try:
                        lang_pred = fasttext_model.predict(buffered_text, k=1)
                        detected_lang = lang_pred[0][0].replace("__label__", "")

                        translated = translator.translate(buffered_text, src=detected_lang, dest='tr').text

                        print(f"Orjinal [{detected_lang}]: {buffered_text}")
                        print(f"Çeviri [tr]: {translated}")

                        await send_subtitle(translated)
                        queue_tts(translated, 'tr')

                        buffered_text = ""

                    except Exception as e:
                        print("Çeviri hatası:", e)
                        buffered_text = ""

async def main(url):
    server = websockets.serve(subtitle_server, "0.0.0.0", 8000)
    await server
    print("WebSocket sunucusu 8000 portunda çalışıyor.")
    await recognize_and_translate(url)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Çoklu dil → Türkçe altyazı ve sesli okuma")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Program durduruldu.")
