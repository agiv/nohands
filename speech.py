"""Голос Piper (irina) вместо spd-say, у которого espeak-ng звучит неразборчиво.
Модель грузится при первой фразе и остаётся в памяти."""
import os, subprocess

MODEL = os.path.expanduser(
    "~/.local/share/nohands/models/piper/ru_RU-irina-medium.onnx")
_voice = None

# Цифровой выход (S/PDIF) PipeWire в простое усыпляет, а приёмник после
# пробуждения ловит сигнал не сразу — короткий звук пропадает целиком, у фразы
# пропадает начало. Замер 25.10 на спящем выходе: тишина перед звуком 0,15 с —
# не слышно, 0,3 с — слышно целиком; нули в том же потоке будят так же.
WAKE_PAD_S = 0.4


def synth(text, exact=False):
    """Фраза в память: (PCM s16 mono байтами, частота дискретизации).

    Piper подмешивает в синтез случайный шум, и одна и та же фраза каждый раз
    звучит чуть иначе. exact=True выключает шум: звук повторяется байт в байт,
    это нужно тестам, чтобы результат не менялся от прогона к прогону."""
    global _voice
    from piper import PiperVoice, SynthesisConfig
    if _voice is None:
        _voice = PiperVoice.load(MODEL)
    cfg = SynthesisConfig(noise_scale=0, noise_w_scale=0) if exact else None
    pcm = b"".join(c.audio_int16_bytes for c in _voice.synthesize(text, syn_config=cfg))
    return pcm, _voice.config.sample_rate


def say(text):
    """Произнести фразу и дождаться, пока она договорится."""
    pcm, rate = synth(text)
    pcm = bytes(int(rate * WAKE_PAD_S) * 2) + pcm
    subprocess.run(["pw-play", "--raw", "--rate", str(rate), "--channels", "1",
                    "--format", "s16", "-"],
                   input=pcm, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
