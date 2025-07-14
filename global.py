import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
import fasttext
import os
from gtts import gTTS
import tempfile
from queue import Queue
import threading
import re
import difflib

SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
VOSK_MODEL_DIR = "/root/"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"

assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"
fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
clients = set()
tts_queue = Queue()

class TextBrainLite:
    def __init__(self):
        self.last_text = ""
        self.colloquials = {
            "geldi mi o": "O mu geldi",
            "bilmiyorum ya": "Bilmiyorum",
            "yani düşündüm": "Sanırım düşündüm",
            "yok yok": "Hayır",
            "tamam tamam": "Tamam",
            "o da olabilir": "Belki de olabilir"
        }
        self.filler_words = ["şey", "ııı", "falan", "hani", "işte", "yani", "hmm", "neyse"]

    def clean_fillers(self, text):
        pattern = r'\b(' + '|'.join(map(re.escape, self.filler_words)) + r')\b'
        return re.sub(pattern, '', text, flags=re.IGNORECASE).strip()

    def rewrite_colloquials(self, text):
        for k, v in self.colloquials.items():
            text = re.sub(rf"\b{k}\b", v, text, flags=re.IGNORECASE)
        return text

    def deduplicate(self, text):
        ratio = difflib.SequenceMatcher(None, text.lower(), self.last_text.lower()).ratio()
        if ratio > 0.92:
            return None
        self.last_text = text
        return text

    def punctuate(self, text):
        text = text.strip()
        if not text.endswith(('.', '?', '!')):
            text += '.'
        return text[0].upper() + text[1:]

    def process(self, raw_text):
        x = self.clean_fillers(raw_text)
        x = self.rewrite_colloquials(x)
        x = self.punctuate(x)
        x = self.deduplicate(x)
        return x

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
                print(f"[TTS Hatası]: {e}")
        tts_queue.task_done()

threading.Thread(target=tts_worker, daemon=True).start()

def queue_tts(text):
    if len(text.strip()) > 10 and not text.isnumeric():
        tts_queue.put(text)

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
    return subprocess.Popen([
        'ffmpeg', '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le', '-'
    ], stdout=subprocess.PIPE).stdout

def detect_language(text):
    pred = fasttext_model.predict(text, k=1)
    lang = pred[0][0].replace("__label__", "")
    return lang

def load_vosk_model(lang_code):
    model_path = os.path.join(VOSK_MODEL_DIR, lang_code)
    if not os.path.exists(model_path):
        raise Exception(f"Vosk modeli yok: {lang_code}")
    return Model(model_path)

async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    initial_recognizer = KaldiRecognizer(Model(os.path.join(VOSK_MODEL_DIR, "en")), SAMPLE_RATE)

    print("[+] Dil tespiti yapılıyor...")
    sample = audio_stream.read(CHUNK_SIZE * 3)
    initial_recognizer.AcceptWaveform(sample)
    first_result = json.loads(initial_recognizer.Result())
    first_text = first_result.get("text", "")
    detected_lang = detect_language(first_text)
    print(f"[+] Tespit edilen dil: {detected_lang}")

    model = load_vosk_model(detected_lang)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)

    brain = TextBrainLite()

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            original = result.get("text", "").strip()
            if not original:
                continue

            processed = brain.process(original)
            if not processed:
                continue

            print(f"\n[Orijinal - {detected_lang}]: {original}")
            print(f"[Doğal TR]: {processed}")
            await send_subtitle(processed)
            queue_tts(processed)

async def main(url):
    server = websockets.serve(subtitle_server, "0.0.0.0", 8000)
    await server
    print("[+] WebSocket altyazı servisi hazır. Port: 8000")
    await recognize_and_translate(url)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lite Gerçek Zamanlı Çeviri Sistemi")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("[X] Durdu.")
