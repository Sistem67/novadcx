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
import re

# Ayarlar
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

# Yükleme kontrolleri
assert os.path.exists(VOSK_MODEL_PATH), "Vosk modeli eksik!"
assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"

model = Model(VOSK_MODEL_PATH)
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
translator = Translator()
clients = set()
tts_queue = Queue()

# TTS Sesli okuma işçisi
def tts_worker():
    while True:
        text = tts_queue.get()
        if text:
            try:
                with tempfile.NamedTemporaryFile(delete=True, suffix=".mp3") as fp:
                    tts = gTTS(text=text, lang='tr')
                    tts.save(fp.name)
                    os.system(f"mpg123 -q {fp.name}")
            except Exception as e:
                print(f"TTS Hatası: {e}")
        tts_queue.task_done()

threading.Thread(target=tts_worker, daemon=True).start()

def queue_tts(text):
    if len(text.strip()) > 15 and not text.isnumeric():
        tts_queue.put(text)

# Altyazı websocket
async def subtitle_server(websocket, path):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

# Altyazıyı gönder
async def send_subtitle(text):
    if clients:
        await asyncio.wait([client.send(text) for client in clients])

# Yayın sesini al
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

# Profesyonel çeviri filtresi
def clean_text(text):
    # Gereksiz tekrarları, simgeleri temizle
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ.,!?-]', '', text)
    return text.strip()

# Tanıma ve çeviri
async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    buffered_text = ""

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            original_text = result.get('text', '').strip()

            if original_text:
                buffered_text += " " + original_text
                if len(buffered_text) >= 40:
                    try:
                        clean_input = clean_text(buffered_text)

                        # Dil tahmini
                        lang_pred = fasttext_model.predict(clean_input, k=1)
                        detected_lang = lang_pred[0][0].replace("__label__", "")

                        # Türkçeye çeviri
                        translated = translator.translate(clean_input, src=detected_lang, dest='tr').text
                        translated = clean_text(translated)

                        # Log ve çıktı
                        print(f"\n[Orjinal - {detected_lang}]: {clean_input}")
                        print(f"[Türkçe Çeviri]: {translated}")

                        await send_subtitle(translated)
                        queue_tts(translated)

                        buffered_text = ""

                    except Exception as e:
                        print(f"Çeviri hatası: {e}")
                        buffered_text = ""

# Ana program
async def main(url):
    server = websockets.serve(subtitle_server, "0.0.0.0", 8000)
    await server
    print("WebSocket altyazı servisi 8000 portunda.")
    await recognize_and_translate(url)

# Çalıştır
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Tam Otomatik Çeviri Backend")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Durdu.")
