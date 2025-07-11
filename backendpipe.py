import subprocess
import os
import sys
import wave
import tempfile
from transformers import pipeline
import argostranslate.package
import argostranslate.translate
from TTS.utils.synthesizer import Synthesizer

WHISPER_PATH = "/root/whisper.cpp/main"
WHISPER_MODEL = "/root/whisper.cpp/models/ggml-medium.bin"

simplifier = pipeline("text2text-generation", model="t5-small")

TTS_CONFIG = "/root/.local/share/tts/tts_models--tr--mai--medium/config.yaml"
TTS_MODEL = "/root/.local/share/tts/tts_models--tr--mai--medium/tr_model.onnx"

synth = Synthesizer(
    model_path=TTS_MODEL,
    config_path=TTS_CONFIG,
    use_cuda=False
)

installed = argostranslate.package.get_installed_packages()
if not installed:
    print("Argos Translate paketi eksik. Lütfen en-tr yükleyin.")
    exit(1)

if len(sys.argv) < 2:
    print("Kullanım: python3 backend_sade.py <m3u8_yayin_linki>")
    exit(1)

stream_url = sys.argv[1]
print("\n[ Aydın Translate Engine v2] Canlı yayın dinleniyor...\n")

ffmpeg_cmd = [
    "ffmpeg", "-i", stream_url,
    "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000",
    "-loglevel", "quiet", "-"
]
ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE)
buffer_size = 16000 * 2 * 5

while True:
    audio = ffmpeg_proc.stdout.read(buffer_size)
    if len(audio) == 0:
        continue

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        wav_path = tmp.name
        with wave.open(wav_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio)

    whisper_cmd = [WHISPER_PATH, "-m", WHISPER_MODEL, "-f", wav_path, "-nt", "-l", "auto"]
    result = subprocess.run(whisper_cmd, stdout=subprocess.PIPE, text=True)
    os.remove(wav_path)

    lines = result.stdout.strip().split("\n")
    if not lines:
        continue

    metin = lines[-1].strip()
    if not metin:
        continue

    print("Algılandı:", metin)

    try:
        sadelesmis = simplifier("simplify: " + metin, max_length=30)[0]["generated_text"]
    except:
        sadelesmis = metin

    print("Sadeleştirilmiş:", sadelesmis)

    try:
        ceviri = argostranslate.translate.get_translation("en", "tr").translate(sadelesmis)
    except:
        ceviri = sadelesmis

    print("Türkçe:", ceviri)

    try:
        wav = synth.tts(ceviri)
        synth.save_wav(wav, "out.wav")
        os.system("mpg123 out.wav")
    except:
        print("[TTS] Ses oluşturulamadı.\n")
        continue
