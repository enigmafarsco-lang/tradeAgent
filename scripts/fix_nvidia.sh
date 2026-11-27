#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# راه‌اندازی کارت NVIDIA (RTX 4050) وقتی نصب است ولی ماژولش بار نمی‌شود.
#
#   ./scripts/fix_nvidia.sh              # تشخیص + تلاش برای نصب ماژول امضاشده
#   ./scripts/fix_nvidia.sh --check      # فقط تشخیص، هیچ تغییری نده
#   ./scripts/fix_nvidia.sh --mok        # آماده‌سازی ثبت کلید MOK (نیاز به ریبوت)
#
# علت رایج: Secure Boot روشن + ماژول DKMS امضانشده → "Key was rejected by service".
# راه بدون BIOS: بسته‌های امضاشدهٔ اوبونتو (linux-modules-nvidia-*-generic).
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CHECK_ONLY=0
DO_MOK=0

for a in "$@"; do
  case "$a" in
    --check) CHECK_ONLY=1 ;;
    --mok) DO_MOK=1 ;;
    -h|--help) sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$a"; exit 2 ;;
  esac
done

ok()   { printf '\033[1;32m[gpu]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[gpu]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[gpu]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[gpu] %s\033[0m\n' "$*"; }
step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }

run_sudo() {  # اجرای دستور با sudo، خروجی هم گرفته می‌شود هم دیده می‌شود
  sudo "$@" 2>&1
}

step "1/5 وضعیت فعلی"
KERNEL="$(uname -r)"
FLAVOR="${KERNEL##*-}"   # generic / lowlatency (آخرین بخش نسخهٔ کرنل)
echo "  kernel      : $KERNEL"
echo "  nvidia-smi  : $(nvidia-smi --query-gpu=name --format=csv,noheader 2>&1 | head -n1)"
if [[ -r /proc/driver/nvidia/version ]]; then
  ok "ماژول NVIDIA بار شده است: $(head -n1 /proc/driver/nvidia/version)"
  ok "کاری لازم نیست - ollama را یک‌بار ری‌استارت کن: systemctl --user restart ollama"
  exit 0
fi
warn "ماژول NVIDIA بار نشده است"
MODV="$(modinfo -F version nvidia 2>/dev/null || true)"
echo "  module on disk: ${MODV:-not found for this kernel}"
SECURE="no"
if command -v mokutil >/dev/null 2>&1 && mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then SECURE="yes"; fi
echo "  secure boot : $SECURE"
PKGS="$(dpkg -l 2>/dev/null | awk '/^ii +nvidia-driver-[0-9]/{print $2}' | tr '\n' ' ')"
echo "  drivers     : ${PKGS:-none}"
DKMS_N="$(dkms status 2>/dev/null | grep -c nvidia || true)"
echo "  dkms entries: ${DKMS_N}"

if (( CHECK_ONLY )); then
  step "2/5 پیام واقعی هسته"
  dim "برای دیدن دلیل دقیق: sudo modprobe nvidia"
  exit 0
fi

step "2/5 دلیل بار نشدن ماژول"
LOAD_MSG="$(run_sudo modprobe nvidia || true)"
if [[ -r /proc/driver/nvidia/version ]]; then
  ok "ماژول حالا بار شد: $(head -n1 /proc/driver/nvidia/version)"
  MODULE_OK=1
else
  MODULE_OK=0
  [[ -n "$LOAD_MSG" ]] && printf '%s\n' "$LOAD_MSG" | tail -4 | sed 's/^/    /'
  case "$LOAD_MSG" in
    *"Key was rejected by service"*|*"required key not available"*|*"Bad signature"*)
      bad "امضای ماژول رد شد (Secure Boot)"; SECURE="yes" ;;
    *"module not found"*|*"FATAL: Module nvidia not found"*)
      bad "ماژولی برای این کرنل نصب نیست" ;;
  esac
fi

step "3/5 تلاش: بستهٔ امضاشدهٔ اوبونتو (بدون تغییر BIOS)"
# بالاترین نسخهٔ نصب‌شده (۵۹۵ بر ۵۹۰ اولویت دارد)؛ اگر نصب نبود، پیشنهاد ubuntu-drivers
DRIVER_VER="$(printf '%s\n' $PKGS | grep -o '[0-9]\{3\}' | sort -n | tail -n1)"
if [[ -z "$DRIVER_VER" ]] && command -v ubuntu-drivers >/dev/null 2>&1; then
  DRIVER_VER="$(ubuntu-drivers devices 2>/dev/null | grep -m1 recommended | grep -o 'nvidia-driver-[0-9]\+' | grep -o '[0-9]\+' || true)"
fi
DRIVER_VER="${DRIVER_VER:-595}"
KEEP_PKG="$(printf '%s\n' $PKGS | grep -m1 "nvidia-driver-${DRIVER_VER}" || echo "nvidia-driver-${DRIVER_VER}")"
CANDIDATES=(
  "linux-modules-nvidia-${DRIVER_VER}-open-${FLAVOR}"
  "linux-modules-nvidia-${DRIVER_VER}-${FLAVOR}"
  "linux-modules-nvidia-${DRIVER_VER}-open-${KERNEL}"
  "linux-modules-nvidia-${DRIVER_VER}-${KERNEL}"
)
INSTALLED_SIGNED=0
if (( MODULE_OK == 0 )); then
  if ! command -v apt-cache >/dev/null 2>&1; then
    warn "apt-cache نیست؛ از این مرحله می‌گذرم"
  else
    for pkg in "${CANDIDATES[@]}"; do
      if apt-cache show "$pkg" >/dev/null 2>&1; then
        dim "نصب بستهٔ امضاشده: $pkg"
        if run_sudo apt-get install -y "$pkg"; then
          INSTALLED_SIGNED=1
          break
        fi
      else
        dim "$pkg در مخازن نیست"
      fi
    done
    if (( INSTALLED_SIGNED )); then
      run_sudo modprobe nvidia >/dev/null 2>&1 || true
      if [[ -r /proc/driver/nvidia/version ]]; then
        ok "با ماژول امضاشده بالا آمد: $(head -n1 /proc/driver/nvidia/version)"
        MODULE_OK=1
      else
        warn "بسته نصب شد ولی ماژول هنوز بار نشد - بعد از ریبوت دوباره بررسی کن"
      fi
    fi
  fi
fi

if (( MODULE_OK )); then
  step "5/5 راه‌اندازی ollama روی کارت NVIDIA"
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/    /'
  if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
    if systemctl --user restart ollama.service 2>/dev/null; then
      ok "سرویس ollama ری‌استارت شد - از این به بعد CUDA را انتخاب می‌کند"
    else
      warn "ری‌استارت نشد: systemctl --user restart ollama"
    fi
  else
    warn "سرویس ollama پیدا نشد؛ یک‌بار ./scripts/setup_llm.sh --repair-ollama"
  fi
  echo
  ok "اثبات: ./scripts/setup_llm.sh --hardware   # باید بنویسد 100% GPU روی RTX"
  dim "اگر هم Vulkan فعال مانده باشد، ollama خودش CUDA را ترجیح می‌دهد"
  exit 0
fi

step "4/5 دو راه باقی‌مانده"
if [[ "$SECURE" == "yes" ]]; then
  echo "  الف) ثبت کلید MOK (یک‌بار، بدون تغییر BIOS):"
  MOKDER="$(find /var/lib/shim-signed/mok -name '*.der' 2>/dev/null | head -n1)"
  if [[ -n "$MOKDER" ]]; then
    echo "      sudo mokutil --import $MOKDER"
  else
    echo "      sudo update-secureboot-policy --enroll-key"
  fi
  echo "      # رمز عبور تعیین می‌کنی؛ بعد ریبوت و در صفحهٔ آبی «Enroll MOK» را می‌زنی"
  echo
  echo "  ب) خاموش‌کردن Secure Boot در BIOS (ساده‌ترین، ولی دسترسی فیزیکی می‌خواهد)"
  echo
  if [[ "$(printf '%s\n' $PKGS | wc -l)" -gt 1 ]]; then
    echo "  ج) دو درایور نصب است (${PKGS})؛ فقط ${KEEP_PKG} بماند:"
    for one in $PKGS; do
      [[ "$one" == "$KEEP_PKG" ]] && continue
      echo "      sudo apt purge $one"
    done
    echo "      sudo apt install --reinstall ${KEEP_PKG} nvidia-dkms-${DRIVER_VER}"
  else
    echo "  ج) نصب دوبارهٔ درایور:"
    echo "      sudo apt install --reinstall ${KEEP_PKG} nvidia-dkms-${DRIVER_VER}"
  fi
else
  echo "  Secure Boot خاموش است، پس مشکل DKMS است:"
  echo "      sudo apt install --reinstall nvidia-dkms-${DRIVER_VER}"
  echo "      sudo dkms autoinstall"
fi
echo "      sudo reboot"
echo
dim "بعد از ریبوت: ./scripts/fix_nvidia.sh --check && ./scripts/setup_llm.sh --hardware"
exit 2
