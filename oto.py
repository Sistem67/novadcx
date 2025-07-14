import asyncio
import subprocess
import json
import os
import websockets
from vosk import Model, KaldiRecognizer
import fasttext
from googletrans import Translator
from edge_tts import Communicate
import base64
import tempfile
from aiohttp import web

# === Ayarlar ===
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"
WS_PORT = 8000
HTTP_PORT = 8500

# === Model Yükleme ===
assert os.path.exists(VOSK_MODEL_PATH), "Vosk modeli eksik!"
assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"

model = Model(VOSK_MODEL_PATH)
ft_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
translator = Translator()
clients = set()
stream_url_global = ""

# === WebSocket altyazı sunucusu ===
async def subtitle_server(websocket, path):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def send_subtitle(json_data):
    if clients:
        await asyncio.wait([client.send(json_data) for client in clients])

# === Yayın sesini ffmpeg ile oku ===
def stream_audio(url):
    return subprocess.Popen([
        'ffmpeg', '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1', '-f', 's16le', '-'
    ], stdout=subprocess.PIPE).stdout

# === TTS ile Türkçe ses üret ve base64 olarak dön ===
async def generate_tts_base64(text):
    try:
        tts = Communicate(text=text, voice="tr-TR-AhmetNeural")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            out_path = f.name
        await tts.save(out_path)
        with open(out_path, "rb") as audio_file:
            encoded_audio = base64.b64encode(audio_file.read()).decode('utf-8')
        os.remove(out_path)
        return encoded_audio
    except Exception as e:
        print(f"TTS Hatası: {e}")
        return ""

# === HTTP sunucusu: yayın URL bilgisini tarayıcıya verir ===
async def handle_stream_url(request):
    return web.json_response({"url": stream_url_global})

async def start_http_server():
    app = web.Application()
    app.add_routes([web.get('/stream-url', handle_stream_url)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HTTP_PORT)
    await site.start()

# === Ana akış: ses tanıma, çeviri, gönderim ===
async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    buffer = ""

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get('text', '').strip()

            if text:
                buffer += " " + text

                if len(buffer) >= 40:
                    try:
                        lang = ft_model.predict(buffer, k=1)[0][0].replace("__label__", "")
                        if lang != "tr":
                            translated = translator.translate(buffer, src=lang, dest='tr').text
                        else:
                            translated = buffer

                        print(f"\n[Orijinal - {lang}]: {buffer}")
                        print(f"[Türkçe]: {translated}")

                        audio_b64 = await generate_tts_base64(translated)

                        payload = json.dumps({
                            "text": translated,
                            "audio": audio_b64
                        })
                        await send_subtitle(payload)
                        buffer = ""
                    except Exception as e:
                        print(f"Çeviri hatası: {e}")
                        buffer = ""

# === Ana fonksiyon ===
async def main(url):
    global stream_url_global
    stream_url_global = url
    print(f"WebSocket altyazı servisi başlatılıyor (port {WS_PORT})...")
    await asyncio.gather(
        start_http_server(),
        websockets.serve(subtitle_server, "0.0.0.0", WS_PORT),
        recognize_and_translate(url)
    )

# === Başlatıcı ===
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vosk-Multi Çeviri Sistemi")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Durduruldu.")
