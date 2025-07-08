import asyncio import subprocess import json import websockets from vosk import Model, KaldiRecognizer from googletrans import Translator import fasttext import os from gtts import gTTS import tempfile from queue import Queue import threading

--- Ayarlar ---

SAMPLE_RATE = 16000 CHUNK_SIZE = 4000

Model yolları

VOSK_MODEL_PATH = "/root/vosk-model-small-tr-0.3" FASTTEXT_MODEL_PATH = "lid.176.bin"

Model dosyaları kontrolü

if not os.path.exists(VOSK_MODEL_PATH): raise FileNotFoundError(f"Vosk modeli bulunamadı: {VOSK_MODEL_PATH}") if not os.path.exists(FASTTEXT_MODEL_PATH): raise FileNotFoundError(f"FastText modeli bulunamadı: {FASTTEXT_MODEL_PATH}")

Modelleri yükle

model = Model(VOSK_MODEL_PATH) fasttext_model = fasttext.load_model(FASTTEXT_MODEL_PATH) translator = Translator() clients = set()

TTS kuyruğu ve işçisi

tts_queue = Queue()

def tts_worker(): while True: text, lang = tts_queue.get() try: with tempfile.NamedTemporaryFile(delete=True, suffix=".mp3") as fp: tts = gTTS(text=text, lang=lang) tts.save(fp.name) os.system(f"mpg123 -q {fp.name}") except Exception as e: print(f"[TTS HATA] {e}") tts_queue.task_done()

threading.Thread(target=tts_worker, daemon=True).start()

def queue_tts(text, lang='tr'): tts_queue.put((text, lang))

--- Dil tespiti ve düzeltme ---

def detect_lang_fasttext(text): predictions = fasttext_model.predict(text, k=1) lang_code = predictions[0][0].replace("label", "") return lang_code

def fix_detected_lang(text, detected_lang): common_en_words = ['the', 'is', 'you', 'are', 'hello', 'and', 'to', 'this', 'with'] en_hits = sum(word in text.lower() for word in common_en_words) if detected_lang == 'tr' and en_hits >= 2: print("⚠️ FastText 'tr' dedi ama İngilizce içerik var → düzeltildi: en") return 'en' return detected_lang

--- Websocket Sunucusu ---

async def subtitle_server(websocket, path): clients.add(websocket) print(f"[+] WebSocket bağlandı: {websocket.remote_address}") try: await websocket.wait_closed() finally: clients.remove(websocket) print(f"[-] WebSocket ayrıldı: {websocket.remote_address}")

async def send_subtitle(text): if clients: await asyncio.wait([client.send(text) for client in clients])

--- ffmpeg ile ses akışı ---

def stream_audio(url): return subprocess.Popen([ 'ffmpeg', '-i', url, '-loglevel', 'quiet', '-ar', str(SAMPLE_RATE), '-ac', '1', '-f', 's16le', '-' ], stdout=subprocess.PIPE).stdout

--- Ana işlev: Dinle → Çevir → Altyazı + Sesli Oku ---

async def recognize_and_translate(url): audio_stream = stream_audio(url) recognizer = KaldiRecognizer(model, SAMPLE_RATE) recognizer.SetWords(True) loop = asyncio.get_event_loop()

buffered_text = ""

while True:
    data = audio_stream.read(CHUNK_SIZE)
    if len(data) == 0:
        print("[!] Ses verisi yok. Yayın bitmiş olabilir.")
        break

    if recognizer.AcceptWaveform(data):
        result = json.loads(recognizer.Result())
        text = result.get('text', '').strip()

        if text:
            buffered_text += " " + text

            if len(buffered_text) > 30:
                try:
                    detected_lang = detect_lang_fasttext(buffered_text)
                    detected_lang = fix_detected_lang(buffered_text, detected_lang)

                    # Dilersen burada yayının dili sabitlenebilir:
                    # detected_lang = 'en'

                    if detected_lang == 'tr':
                        translated = translator.translate(buffered_text, dest='en').text
                        tts_lang = 'en'
                    else:
                        translated = translator.translate(buffered_text, dest='tr').text
                        tts_lang = 'tr'

                    print(f"[✔️] Çeviri: {translated}")
                    await send_subtitle(translated)
                    queue_tts(translated, tts_lang)
                    buffered_text = ""

                except Exception as e:
                    print(f"[X] Hata: {e}")

--- Giriş Noktası ---

async def main(url): server = websockets.serve(subtitle_server, "0.0.0.0", 8000) await server print("[🚀] WebSocket sunucusu 8000 portunda hazır.") await recognize_and_translate(url)

if name == "main": import argparse parser = argparse.ArgumentParser(description="Canlı yayından altyazı ve sesli çeviri") parser.add_argument('--url', required=True, help='Canlı yayın M3U8 linki') args = parser.parse_args()

try:
    asyncio.run(main(args.url))
except KeyboardInterrupt:
    print("\n[!] Program elle durduruldu.")

