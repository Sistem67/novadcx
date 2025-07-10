import subprocess
import json
import sys
import os
from vosk import Model, KaldiRecognizer
from TTS.utils.synthesizer import Synthesizer

# VOSK model yolu
vosk_model_path = "/root/vosk-model-small-tr-0.3"

# Coqui TTS model yolları
tts_config_path = "/ttsmodels/config.yaml"
tts_model_path = "/ttsmodels/tr_model.onnx"

# VOSK model yükle
model = Model(vosk_model_path)
rec = KaldiRecognizer(model, 16000)

# Coqui TTS synthesizer yükle
synthesizer = Synthesizer(
    tts_checkpoint=None,
    tts_config_path=tts_config_path,
    tts_speakers_file=None,
    tts_languages_file=None,
    vocoder_checkpoint=None,
    vocoder_config=None,
    encoder_checkpoint=None,
    encoder_config=None,
    use_cuda=False,
    model_path=tts_model_path,
    config_path=tts_config_path
)

# Yayın URL kontrol
if len(sys.argv) < 2:
    print("Kullanım: python3 hata6.py <m3u8_link>")
    sys.exit(1)

url = sys.argv[1]

print("Yayın dinleniyor...\n")

process = subprocess.Popen([
    "ffmpeg", "-i", url,
    "-ar", "16000", "-ac", "1",
    "-f", "s16le", "-loglevel", "quiet", "-"
], stdout=subprocess.PIPE)

while True:
    data = process.stdout.read(4000)
    if len(data) == 0:
        continue

    if rec.AcceptWaveform(data):
        result = json.loads(rec.Result())
        original = result.get("text", "").strip()
        if original:
            print("Metin:", original)
            wav = synthesizer.tts(original)
            synthesizer.save_wav(wav, "out.wav")
            os.system("mpg123 out.wav")
