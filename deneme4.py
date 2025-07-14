import asyncio
import subprocess
import json
import os
import websockets
from vosk import Model, KaldiRecognizer, SpkModel
import fasttext
from googletrans import Translator
from edge_tts import Communicate
import signal

# === Ayarlar ===
SAMPLE_RATE = 16000
CHUNK_SIZE = 8000  # M3U8 için daha büyük buffer
VOSK_MODEL_PATH = "/root/vosk-model-spk-0.4"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"
WS_PORT = 8000
TTS_VOICE = "tr-TR-EmelNeural"
MAX_RETRIES = 3  # M3U8 için yeniden deneme sayısı

# === Model Yükleme ===
assert os.path.exists(VOSK_MODEL_PATH), "Vosk modeli eksik!"
assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"

model = Model(VOSK_MODEL_PATH)
spk_model = SpkModel(os.path.join(VOSK_MODEL_PATH, "spk"))
ft_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
translator = Translator()
clients = set()
ffmpeg_process = None

# === FFmpeg M3U8 Optimizasyonu ===
def start_ffmpeg(url):
    return subprocess.Popen([
        'ffmpeg',
        '-i', url,
        '-loglevel', 'error',
        '-reconnect', '1',  # M3U8 için önemli
        '-reconnect_at_eof', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',  # Max 5 sn bekle
        '-thread_queue_size', '512',
        '-acodec', 'pcm_s16le',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le',
        '-vn',
        '-'
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# === Temizlik ===
def cleanup():
    global ffmpeg_process
    if ffmpeg_process:
        ffmpeg_process.terminate()
        try:
            ffmpeg_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            ffmpeg_process.kill()
    os.system("pkill -f 'mpg123'")

# === Sinyal Yakalama ===
def handle_signal(signum, frame):
    print("\nSistem kapatılıyor...")
    cleanup()
    exit(0)

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# === WebSocket Sunucusu ===
async def subtitle_server(websocket, path):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def send_subtitle(text, speaker=None):
    if clients:
        message = json.dumps({
            "text": text,
            "speaker": speaker or "Unknown",
            "type": "subtitle"
        })
        await asyncio.wait([client.send(message) for client in clients])

# === TTS ===
async def speak_turkish(text):
    try:
        tts = Communicate(text=text, voice=TTS_VOICE)
        await tts.save("output.mp3")
        os.system("mpg123 -q output.mp3 && rm output.mp3")
    except Exception as e:
        print(f"TTS Hatası: {e}")

# === Konuşmacı Tanıma ===
def get_speaker(spk_vec):
    if spk_vec:
        return f"Konuşmacı-{int(sum(spk_vec[:3])%1000:03d}"
    return "Bilinmeyen"

# === Ana İşlem Döngüsü ===
async def process_m3u8(url):
    global ffmpeg_process
    retry_count = 0
    
    while retry_count < MAX_RETRIES:
        try:
            ffmpeg_process = start_ffmpeg(url)
            recognizer = KaldiRecognizer(model, SAMPLE_RATE)
            recognizer.SetWords(True)
            recognizer.SetSpkModel(spk_model)
            
            while True:
                data = ffmpeg_process.stdout.read(CHUNK_SIZE)
                if not data:
                    print("Akış verisi alınamadı, yeniden deneniyor...")
                    await asyncio.sleep(2)
                    break

                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    text = result.get('text', '').strip()
                    spk = result.get('spk', None)
                    
                    if text:
                        speaker = get_speaker(spk)
                        lang = ft_model.predict(text, k=1)[0][0].replace("__label__", "")
                        
                        try:
                            translated = translator.translate(text, src=lang, dest='tr').text
                            print(f"\n[{speaker}][{lang}]: {text}")
                            print(f"[Çeviri]: {translated}")
                            
                            await send_subtitle(translated, speaker)
                            await speak_turkish(translated)
                            
                        except Exception as e:
                            print(f"Çeviri hatası: {e}")

        except Exception as e:
            print(f"Hata oluştu: {str(e)}")
            retry_count += 1
            await asyncio.sleep(5)
            continue
        
        retry_count = 0  # Başarılı akış sonrası sıfırla

# === Ana Fonksiyon ===
async def main(url):
    print(f"SPK Modeli ile M3U8 Çeviri Sistemi Başlatılıyor...")
    print(f"Port: {WS_PORT} | Model: {VOSK_MODEL_PATH}")
    
    server = await websockets.serve(subtitle_server, "0.0.0.0", WS_PORT)
    await process_m3u8(url)
    await server.wait_closed()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True, help='M3U8 yayın URLsi')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
        cleanup()
    except Exception as e:
        print(f"Kritik hata: {str(e)}")
        cleanup()
