#!/bin/bash
# Развернуть nohands с нуля: venv, модели, служба и команда nohands.
# Повторный запуск безопасен — готовое пропускается.
#
#   ./bootstrap.sh
#
# root не нужен. Если не хватает системных пакетов, скрипт напишет, какие
# поставить через apt, и остановится.
set -euo pipefail

CODE=$(cd "$(dirname "$0")" && pwd)
# voiced.py ищет данные именно здесь; переопределять — только для проверки
# самого bootstrap в чистом каталоге.
DATA=${NOHANDS_DATA:-$HOME/.local/share/nohands}
MODELS=$DATA/models
VENV=$DATA/venv
PY=$VENV/bin/python

step() { printf '\n── %s\n' "$*"; }

# ─────────────────────────── системные пакеты ───────────────────────────────
step "системные пакеты"
# numpy и evdev — из системы, venv видит их через --system-site-packages;
# curl качает модели ниже — без него установка упала бы уже после проверки;
# звуки режимов — из sound-theme-freedesktop; pw-play и xclip вызывает демон;
# значок в трее — через libayatana-appindicator (показывает расширение
# ubuntu-appindicators).
APT=(python3-venv python3-numpy python3-evdev libportaudio2 pipewire-bin curl
     xclip sound-theme-freedesktop python3-gi gir1.2-ayatanaappindicator3-0.1)
# libcublas/libcudart нужны whisper только на видеокарте NVIDIA: без неё он
# работает на процессоре, и полгигабайта CUDA ставить незачем.
[ -e /dev/nvidiactl ] && APT+=(libcublas12 libcublaslt12 libcudart12)
missing=()
for p in "${APT[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || missing+=("$p")
done
if ((${#missing[@]})); then
    echo "не хватает: ${missing[*]}"
    echo "поставьте и запустите bootstrap ещё раз:"
    echo "  sudo apt install ${missing[*]}"
    exit 1
fi
echo "всё на месте"

step "доступ к /dev/uinput"
# Без него служба стартует, но мышь и клавиатура молчат. Права даёт правило
# udev с тегом uaccess — и только активной локальной сессии: по SSH доступа
# не будет, даже если за компьютером он есть. Поэтому ищем ещё и само правило.
UDEV_RULE='KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"'
if [ -w /dev/uinput ]; then
    echo "есть"
elif grep -qsE 'KERNEL=="uinput".*uaccess' /etc/udev/rules.d/*.rules \
        /run/udev/rules.d/*.rules /usr/lib/udev/rules.d/*.rules; then
    echo "правило udev есть, но у этой сессии доступа нет (SSH?)"
    echo "за компьютером он появится; если нет — войдите в систему заново"
else
    echo "нет правила udev: без него nohands не нажмёт ни одной клавиши"
    echo "добавьте и запустите bootstrap ещё раз:"
    echo "  echo '$UDEV_RULE' | sudo tee /etc/udev/rules.d/60-nohands-uinput.rules"
    echo "  sudo udevadm control --reload && sudo udevadm trigger /dev/uinput"
    exit 1
fi

# ──────────────────────────────── venv ──────────────────────────────────────
step "venv в $VENV"
mkdir -p "$DATA/logs" "$MODELS"
# В журнал попадает сказанное — чужим его читать незачем
chmod 700 "$DATA" "$DATA/logs"
[ -x "$PY" ] || python3 -m venv --system-site-packages "$VENV"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r "$CODE/requirements.txt"
echo "пакеты установлены"

# ──────────────────────────────── модели ────────────────────────────────────
fetch() {   # url файл — качаем во временный файл, чтобы обрыв не оставил половину
    curl -fL --progress-bar -o "$2.part" "$1" && mv "$2.part" "$2"
}

step "модель команд: vosk"
if [ ! -d "$MODELS/vosk-model-small-ru-0.22" ]; then
    fetch https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip "$MODELS/vosk.zip"
    "$PY" -m zipfile -e "$MODELS/vosk.zip" "$MODELS"
    rm "$MODELS/vosk.zip"
fi
echo "есть"

step "модель лица: mediapipe"
[ -f "$MODELS/face_landmarker.task" ] || fetch \
    https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task \
    "$MODELS/face_landmarker.task"
echo "есть"

step "голос подсказок: piper irina"
[ -f "$MODELS/piper/ru_RU-irina-medium.onnx" ] || \
    "$PY" -m piper.download_voices --data-dir "$MODELS/piper" ru_RU-irina-medium
echo "есть"

step "настройки: ~/.config/nohands/config.toml"
# Свой конфиг не трогаем: bootstrap перезапускают, а правки — ваши.
CONFIG=$HOME/.config/nohands/config.toml
if [ -f "$CONFIG" ]; then
    echo "уже есть, оставляю"
else
    mkdir -p "$(dirname "$CONFIG")"
    cp "$CODE/deploy/config.toml" "$CONFIG"
    echo "положен по умолчанию"
fi

step "модель диктовки: whisper"
# Демон и сам скачал бы её при первой диктовке, но тогда первая фраза ждала
# бы загрузку до полутора гигабайт. Модель — та, что выберет демон.
WHISPER=$(cd "$CODE" && "$PY" -c "import voiced; print(*voiced.whisper_settings())")
echo "$WHISPER"
"$PY" -c "from faster_whisper import download_model as d; d('${WHISPER%% *}', cache_dir='$MODELS/whisper')" >/dev/null
echo "есть"

step "LLM для консоли: ollama"
# Без неё консоль работает на правилах; LLM нужна лишь фразам-намерениям
# вроде «покажи файлы в загрузках». Модель — та, что прочитает демон.
LLM=$(cd "$CODE" && "$PY" -c "import shell; l = shell.load_llm(); print(l.model if l else '')")
if [ -z "$LLM" ]; then
    echo "LLM выключена в настройках"
elif command -v ollama >/dev/null; then
    ollama pull "$LLM" || echo "не скачалась: запустите ollama и повторите"
else
    echo "ollama не найдена — консоль будет работать только по правилам"
    echo "поставить: curl -fsSL https://ollama.com/install.sh | sh"
fi

# ───────────────────────── служба и команда nohands ─────────────────────────
if [ "${NOHANDS_NO_SERVICE:-}" ]; then
    step "служба пропущена (NOHANDS_NO_SERVICE)"
    exit 0
fi
step "служба nohands"
subst() { sed -e "s|@CODE@|$CODE|g" -e "s|@DATA@|$DATA|g" "$1"; }
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/bin"
subst "$CODE/deploy/nohands.service" > "$HOME/.config/systemd/user/nohands.service"
subst "$CODE/deploy/nohands" > "$HOME/.local/bin/nohands"
chmod +x "$HOME/.local/bin/nohands"
systemctl --user daemon-reload
systemctl --user enable nohands
systemctl --user restart nohands
echo "запущена; журнал — nohands log"
