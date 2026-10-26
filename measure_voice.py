#!/usr/bin/env python
"""Замер реального уровня голоса в тех же единицах, что видит арбитр.

Арбитр в voiced.py считает блок речью по правилу rms(блок) > gate, где блок —
125 мс. Логи «фрагмент ... rms=» усредняют РМС по всему фрагменту вместе с
паузами внутри, поэтому занижают речь. Здесь меряем поблочно, как арбитр.

Подсказки идут голосом: смотреть в терминал во время замера не нужно.
"""
import time
import numpy as np
import sounddevice as sd
import speech
from voiced import speech_gate

RATE, BLOCK = 16000, 2000        # те же константы, что в voiced.py
STEP_S = BLOCK / RATE            # 0.125 с
PAUSE_S = 0.3                    # арбитрская пауза, закрывающая поток речи
CANDIDATES = (80, 100, 128, 150, 200, 260)


def say(text):
    speech.say(text)
    time.sleep(0.3)


def capture(seconds):
    """Вернуть rms каждого 125-мс блока за указанное время."""
    out = []
    with sd.InputStream(samplerate=RATE, channels=1,
                        dtype="int16", blocksize=BLOCK) as st:
        for _ in range(int(seconds / STEP_S)):
            data, _ = st.read(BLOCK)
            pcm = data.reshape(-1).astype(np.float32)
            out.append(float(np.sqrt(np.mean(pcm ** 2))))
    return np.array(out)


def longest_gap(rms, gate):
    """Самая длинная череда блоков ниже порога, в секундах.

    Это и есть «ложная тишина»: если внутри связной речи она дотягивает до
    PAUSE_S, арбитр считает, что поток речи кончился.
    """
    best = cur = 0
    for v in rms:
        cur = cur + 1 if v <= gate else 0
        best = max(best, cur)
    return best * STEP_S


def pct(a, q):
    return float(np.percentile(a, q))


print("Замер начнётся по голосовой подсказке. В терминал смотреть не нужно.\n")
say("Замер уровня. Сначала помолчите две секунды")
noise = capture(2.0)

say("Теперь говорите обычным голосом, пока не скажу хватит")
talk = capture(12.0)
say("Хватит")

say("Теперь скажите слово таб, пауза, слово центр, пауза, слово стоп")
cmds = capture(8.0)
say("Замер закончен")

noise_med = float(np.median(noise))
gate_now = speech_gate(noise_med)

print(f"\nШУМ КОМНАТЫ (2 с молчания)")
print(f"  медиана={noise_med:.0f}  разброс={noise.min():.0f}..{noise.max():.0f}")
print(f"  -> порог, который поставит калибровка voiced.py: {gate_now:.0f}")
print(f"     (формула voiced.speech_gate; шум*2 = {noise_med * 2:.0f})")

for name, rms in (("СВЯЗНАЯ РЕЧЬ (12 с)", talk), ("КОРОТКИЕ КОМАНДЫ (8 с)", cmds)):
    print(f"\n{name}")
    print(f"  rms блоков: p10={pct(rms,10):.0f} p25={pct(rms,25):.0f} "
          f"медиана={pct(rms,50):.0f} p75={pct(rms,75):.0f} "
          f"p90={pct(rms,90):.0f} max={rms.max():.0f}")
    print(f"  {'порог':>6} {'блоков громче':>14} {'ложная тишина':>15}")
    for g in CANDIDATES:
        loud = float((rms > g).mean()) * 100
        gap = longest_gap(rms, g)
        flag = "  <-- сейчас" if abs(g - gate_now) < 1 else ""
        warn = " ПРОТЕЧКА" if loud == 0 else (" рвёт поток" if gap >= PAUSE_S else "")
        print(f"  {g:>6} {loud:>13.0f}% {gap:>14.2f}с{warn}{flag}")

print(f"\nЧитать так: «блоков громче» — какую долю речи арбитр вообще видит.")
print(f"«Ложная тишина» — самый длинный провал ниже порога; если он >= {PAUSE_S} с,")
print(f"арбитр считает поток речи законченным прямо посреди фразы.")
print(f"Если доля громких блоков 0%, счётчик _closed не сбрасывается никогда,")
print(f"и барьер «рядом сказано N слов» копит слова без конца.")
