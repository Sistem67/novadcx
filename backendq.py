import argparse
import asyncio
import json
import os
import subprocess
import wave
import io

import fasttext
import torch
from fastapi import FastAPI, WebSocket
from transformers import MarianMTModel, MarianTokenizer
from vosk import Model, KaldiRecognizer
from piper import PiperVoice  # Güncel Piper importu

# Ayarlar
VOSK_MODEL_PATH = "vosk-model-small-tr-0.15"
FASTTEXT_MODEL_PATH = "lid.176.bin"
WS_HOST = "0.0.0.0"
WS_PORT = 8000
TARGET_LANG = "tr"

# Model yolları
PIPER_MODEL_DIR = os.path.expanduser("~/xdcx/models")
PIPER_MODEL_PATH = os.path.join(PIPER_MODEL_DIR, "tr_TR-fahrettin-medium.onnx")
PIPER_CONFIG_PATH = os.path.join(PIPER_MODEL_DIR, "tr_TR-fahrettin-medium.onnx.json")

print("Modeller yükleniyor...")

# Model yüklemeleri
ft_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
tokenizer = MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-en-tr")
translation_model = MarianMTModel.from_pretrained("Helsinki-NLP/opus-mt-en-tr")
vosk_model = Model(VOSK_MODEL_PATH)
recognizer = KaldiRecognizer(vosk_model, 16000)

# Piper TTS Yükleme
try:
    voice = PiperVoice.load(
        model_path=PIPER_MODEL_PATH,
        config_path=PIPER_CONFIG_PATH,
        use_cuda=False
    )
    print("Piper TTS modeli başarıyla yüklendi.")
except Exception as e:
    print(f"Piper TTS modeli yüklenemedi: {e}")
    raise

app = FastAPI()
connected_clients = set()

# WAV to PCM16 Dönüşüm Fonksiyonu (Alternatif)
def convert_wav_to_pcm16(wav_bytes):
    """Piper'ın çıktısını PCM16'ya dönüştürür"""
    with wave.open(io.BytesIO(wav_bytes), 'rb') as wav_file:
        frames = wav_file.readframes(wav_file.getnframes())
        return frames

async def broadcast(message):
    if connected_clients:
        await asyncio.wait([client.send_text(message) for client in connected_clients])

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    finally:
        connected_clients.remove(websocket)

async def run_ffmpeg(url, queue):
    command = [
        "ffmpeg",
        "-i", url,
        "-loglevel", "quiet",
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        "pipe:1"
    ]
    process = await asyncio.create_subprocess_exec(*command, stdout=subprocess.PIPE)
    try:
        while True:
            data = await process.stdout.read(4000)
            if not data:
                break
            await queue.put(data)
    finally:
        process.kill()

async def speech_recognizer(queue):
    while True:
        data = await queue.get()
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get("text", "").strip()
            if text:
                lang = ft_model.predict(text)[0][0].replace("__label__", "")
                translated = translate_text(text) if lang != TARGET_LANG and len(text.split()) > 1 else text
                
                # Ses sentezi
                audio = synthesize_speech(translated)
                
                await broadcast(json.dumps({
                    "type": "subtitle",
                    "text": translated,
                }))
        queue.task_done()

def translate_text(text):
    batch = tokenizer(text, return_tensors="pt", padding=True)
    translated = translation_model.generate(**batch)
    return tokenizer.decode(translated[0], skip_special_tokens=True)

def synthesize_speech(text):
    try:
        wav_bytes = voice.synthesize(text)
        return convert_wav_to_pcm16(wav_bytes)  # Özel dönüşüm fonksiyonu
    except Exception as e:
        print(f"Ses sentezi hatası: {e}")
        return None

async def main(url):
    queue = asyncio.Queue()
    await asyncio.gather(
        run_ffmpeg(url, queue),
        speech_recognizer(queue),
        uvicorn.Server(uvicorn.Config(app, host=WS_HOST, port=WS_PORT)).serve()
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str, required=True)
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Sunucu kapatıldı.")
