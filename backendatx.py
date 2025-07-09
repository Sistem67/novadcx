import sys
import subprocess
import queue
import threading
import wave
import json
import argparse

import vosk
import pyaudio
import argostranslate.package
import argostranslate.translate

from TTS.api import TTS  # Coqui TTS

# --- ARGOS TRANSLATE MODEL KONTROL ---
def load_argos_model():
    installed_languages = argostranslate.translate.get_installed_languages()
    from_lang = next((lang for lang in installed_languages if lang.code == "en"), None)
    to_lang = next((lang for lang in installed_languages if lang.code == "tr"), None)
    if from_lang is None or to_lang is None:
        print("[ERROR] EN→TR modeli yüklü değil. Lütfen şu komutu çalıştır:\nargos-translate-cli --install --from-lang en --to-lang tr")
        sys.exit(1)
    return from_lang.get_translation(to_lang)

translation = load_argos_model()

# --- COQUI TTS MODEL ---
tts = TTS(model_name="tts_models/tr/commonvoice/vits")

# --- VOSK MODEL YOLU ---
VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"  # değiştir varsa kendi yolun

# --- Ses İşleme ---

q = queue.Queue()

def audio_callback(in_data, frame_count, time_info, status):
    q.put(in_data)
    return (None, pyaudio.paContinue)

def recognize_and_translate():
    model = vosk.Model(VOSK_MODEL_PATH)
    rec = vosk.KaldiRecognizer(model, 16000)

    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=8000,
                    stream_callback=audio_callback)

    stream.start_stream()
    print("[INFO] Canlı yayından ses alınıyor...")

    try:
        while True:
            data = q.get()
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                text = result.get("text", "")
                if text.strip():
                    print(f"Orijinal: {text}")
                    translated = translation.translate(text)
                    print(f"Çeviri: {translated}")

                    # Seslendir
                    tts.tts_to_file(text=translated, file_path="tts_output.wav")
                    # Çal
                    subprocess.run(["aplay", "tts_output.wav"])
    except KeyboardInterrupt:
        print("\n[INFO] Program sonlandırıldı.")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

# --- Ana ---

def main():
    parser = argparse.ArgumentParser(description="Canlı yayından ses al, çevir ve seslendir.")
    parser.add_argument("--url", type=str, required=True, help="M3U8 canlı yayın linki")
    args = parser.parse_args()

    # ffmpeg ile m3u8 → 16kHz mono PCM pipe
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", args.url,
        "-loglevel", "quiet",
        "-ar", "16000",
        "-ac", "1",
        "-f", "s16le",
        "-"
    ]

    # ffmpeg stdout'u okuyup PyAudio'ya ver
    ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE)

    model = vosk.Model(VOSK_MODEL_PATH)
    rec = vosk.KaldiRecognizer(model, 16000)

    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    output=True)

    print("[INFO] Yayından ses ve çeviri başlıyor... Ctrl+C ile durdurun.")

    try:
        while True:
            data = ffmpeg_process.stdout.read(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                text = result.get("text", "")
                if text.strip():
                    print(f"Orijinal: {text}")
                    translated = translation.translate(text)
                    print(f"Çeviri: {translated}")

                    # Coqui TTS ile seslendir
                    tts.tts_to_file(text=translated, file_path="tts_output.wav")
                    subprocess.run(["aplay", "tts_output.wav"])
    except KeyboardInterrupt:
        print("\n[INFO] Program sonlandırıldı.")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        ffmpeg_process.kill()


if __name__ == "__main__":
    main()
