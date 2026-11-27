#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# راه‌اندازی کارت NVIDIA (RTX 4050) وقتی نصب است ولی ماژولش بار نمی‌شود.
#
#   ./scripts/fix_nvidia.sh              # تشخیص + تلاش برای نصب ماژول امضاشده
#   ./scripts/fix_nvidia.sh --check      # فقط تشخیص، هیچ تغییری نده
#   ./scripts/fix_nvidia.sh --mok        # آماده‌سازی ثبت کلید MOK (نیاز به ریبوت)
#   ./scripts/fix_nvidia.sh --deep       # وقتی ماژول "No such device" می‌دهد: دلیل را پیدا کن
#   ./scripts/fix_nvidia.sh --nouveau-off # اگر nouveau کارت را گرفته باشد
#   ./scripts/fix_nvidia.sh --pci-reset   # اگر درایور کارت را در سطح PCI نمی‌بیند
#
# علت رایج: Secure Boot روشن + ماژول DKMS امضانشده → "Key was rejected by service".
# راه بدون BIOS: بسته‌های امضاشدهٔ اوبونتو (linux-modules-nvidia-*-generic).
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CHECK_ONLY=0
DO_MOK=0
DEEP=0
NOUVEAU_OFF=0
PCI_RESET=0

for a in "$@"; do
  case "$a" in
    --check) CHECK_ONLY=1 ;;
    --mok) DO_MOK=1 ;;
    --deep) DEEP=1 ;;
    --nouveau-off) NOUVEAU_OFF=1 ;;
    --pci-reset) PCI_RESET=1 ;;
    -h|--help) sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$a"; exit 2 ;;
  esac
done

ok()   { printf '\033[1;32m[gpu]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[gpu]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[gpu]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[gpu] %s\033[0m\n' "$*"; }
step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }

unit_add_env() {  # unit_add_env KEY VALUE → در سرویس کاربری ollama
  local unit="$HOME/.config/systemd/user/ollama.service"
  [[ -f "$unit" ]] || return 1
  grep -q "^Environment=$1=" "$unit" && return 0
  python3 - "$unit" "$1" "$2" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
lines = p.read_text().splitlines()
out, done = [], False
for ln in lines:
    out.append(ln)
    if ln.startswith("ExecStart=") and not done:
        out.append(f"Environment={sys.argv[2]}={sys.argv[3]}")
        done = True
p.write_text("\n".join(out) + "\n")
PY
  systemctl --user daemon-reload 2>/dev/null || true
  return 0
}

free_nouveau() {
  step "بلک‌لیست کردن nouveau و آزادکردن کارت"
  sudo -v 2>/dev/null || { bad "sudo لازم است"; return 1; }
  printf '%s\n' 'blacklist nouveau' 'options nouveau modeset=0' \
    | sudo tee /etc/modprobe.d/blacklist-nouveau.conf >/dev/null || return 1
  ok "نوشته شد: /etc/modprobe.d/blacklist-nouveau.conf"
  sudo modprobe -r nouveau 2>/dev/null && ok "nouveau از حافظه برداشته شد" \
    || warn "nouveau در حال استفاده است؛ بعد از ریبوت آزاد می‌شود"
  sudo update-initramfs -u >/dev/null 2>&1 && ok "initramfs به‌روز شد"
  sudo modprobe nvidia 2>&1 | tail -2
  if [[ -r /proc/driver/nvidia/version ]]; then
    ok "ماژول nvidia حالا بار شد: $(head -n1 /proc/driver/nvidia/version)"
    nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | sed 's/^/    /'
  else
    warn "هنوز بار نشد؛ بعد از ریبوت دوباره: ./scripts/fix_nvidia.sh --check"
    echo "      sudo reboot"
  fi
  return 0
}

pci_reset_gpu() {
  local bdf
  bdf="$(lspci -nn 2>/dev/null | awk '/NVIDIA/ && /VGA|3D/{print $1; exit}')"
  [[ -n "$bdf" ]] || { bad "کارت NVIDIA در lspci نیست"; return 1; }
  [[ "$bdf" != *:*:* ]] && bdf="0000:$bdf"
  step "ریست PCI کارت $bdf"
  sudo -v 2>/dev/null || { bad "sudo لازم است"; return 1; }
  sudo modprobe -r nvidia 2>/dev/null || true
  sudo sh -c "echo 1 > /sys/bus/pci/devices/$bdf/remove" 2>&1 | tail -2
  sudo sh -c "echo 1 > /sys/bus/pci/rescan" 2>&1 | tail -2
  sleep 2
  sudo modprobe nvidia 2>&1 | tail -2
  if [[ -r /proc/driver/nvidia/version ]]; then
    ok "بعد از ریست، ماژول بار شد"
    nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | sed 's/^/    /'
  else
    warn "ریست کافی نبود؛ احتمالاً درایور غیر-آزاد لازم است:"
    echo "      sudo apt install nvidia-driver-595 && sudo reboot"
  fi
  return 0
}

deep_diag() {
  local out="$ROOT/var/logs/nvidia-diag.txt" verdict="" bdf drv
  mkdir -p "$ROOT/var/logs"
  step "تشخیص عمیق (همه‌چیز در $out)"
  if ! sudo -v 2>/dev/null; then
    bad "برای خواندن dmesg و بارگذاری ماژول، sudo لازم است"
    return 1
  fi
  bdf="$(lspci -nn 2>/dev/null | awk '/NVIDIA/ && /VGA|3D/{print $1; exit}')"
  # sysfs همیشه دامنهٔ چهاررقمی می‌خواهد: 56:00.0 → 0000:56:00.0
  [[ -n "$bdf" && "$bdf" != *:*:* ]] && bdf="0000:$bdf"
  {
    echo "date      : $(date -Is)"
    echo "kernel    : $(uname -r)"
    echo "cmdline   : $(cat /proc/cmdline)"
    echo "virt      : $(systemd-detect-virt 2>/dev/null || echo none)"
    echo "secureboot: $(mokutil --sb-state 2>/dev/null | head -n1)"
    echo
    echo "=== lspci (nvidia) ==="
    lspci -nnk 2>/dev/null | grep -A4 -i 'nvidia' || echo "no nvidia device in lspci"
    echo
    echo "=== modules ==="
    lsmod | grep -Ei 'nouveau|nvidia|vfio|i915' || echo "none loaded"
    echo
    echo "=== modprobe nvidia ==="
    sudo modprobe nvidia 2>&1; echo "rc=$?"
    echo
    echo "=== dmesg (nvidia) ==="
    sudo dmesg 2>/dev/null | grep -iE 'nvidia|nvrm|nouveau' | tail -40 || echo "empty"
    echo
    echo "=== devices ==="
    ls -l /dev/nvidia* 2>&1 | head -5
    echo
    echo "=== packages ==="
    dpkg -l 2>/dev/null | awk '/nvidia-(driver|dkms|kernel|firmware|utils)/{print $2, $3}'
  } > "$out" 2>&1

  drv="$(awk -F': ' '/Kernel driver in use/{print $2; exit}' "$out")"
  echo "  kernel driver in use on the GPU: ${drv:-<none>}"
  echo "  log: $out"
  echo
  step "حکم"
  if [[ -r /proc/driver/nvidia/version ]]; then
    ok "ماژول بار شد: $(head -n1 /proc/driver/nvidia/version)"
    return 0
  fi
  local txt; txt="$(grep -iE 'nvrm|nvidia|nouveau' "$out" | tr 'A-Z' 'a-z')"
  if [[ "$drv" == "nouveau" ]]; then
    verdict="nouveau holds the GPU (that is why NVRM says 'No NVIDIA GPU found')"
  elif [[ "$drv" == vfio* ]]; then
    verdict="vfio-pci holds the GPU"
  else
    case "$txt" in
      *"probe routine was not called"*) verdict="another driver owns the device" ;;
      *"no nvidia gpu found"*|*"no devices found"*) verdict="the driver loaded but sees no GPU at the PCI level" ;;
      *"unknown symbol"*|*"invalid module format"*) verdict="kernel/module mismatch - the module is not for this kernel" ;;
      *"key was rejected"*) verdict="signature refused (secure boot)" ;;
    esac
  fi
  [[ -z "$verdict" && ( -z "$drv" || "$drv" == "nvidia" ) ]] && verdict="no driver is bound to the GPU"
  warn "نتیجه: ${verdict:-نامشخص - فایل $out را ببین}"
  echo
  case "$verdict" in
    *nouveau*)
      echo "  رفع: nouveau باید بلک‌لیست شود تا nvidia بتواند کارت را بگیرد:"
      echo "    sudo modprobe -r nouveau"
      echo "    echo 'blacklist nouveau' | sudo tee /etc/modprobe.d/blacklist-nouveau.conf"
      echo "    sudo update-initramfs -u && sudo reboot" ;;
    *vfio*)
      echo "  رفع: شناسهٔ کارت را از vfio بردار و ریبوت کن"
      echo "    grep -r vfio /etc/modprobe.d/ /etc/default/grub" ;;
    *"no GPU at the PCI level"*)
      echo "  یعنی درایور نصب است ولی کارت در دسترس نیست. دو کار:"
      echo "    1) کارت را ریست کن:"
      echo "       sudo sh -c 'echo 1 > /sys/bus/pci/devices/${bdf:-0000:56:00.0}/remove'"
      echo "       sudo sh -c 'echo 1 > /sys/bus/pci/rescan'"
      echo "       sudo modprobe nvidia && nvidia-smi"
      echo "    2) اگر جواب نداد، درایور غیر-آزاد (بدون -open) را امتحان کن:"
      echo "       sudo apt install nvidia-driver-595   # بدون open"
      echo "       sudo reboot" ;;
    *mismatch*)
      echo "  رفع: بستهٔ ماژول همان کرنل را نصب کن:"
      echo "    sudo apt install linux-modules-nvidia-595-open-$(uname -r)" ;;
    *)
      echo "  پیشنهاد: درایور غیر-آزاد را امتحان کن (روی بعضی لپ‌تاپ‌ها open با آن مشکل دارد):"
      echo "    sudo apt install nvidia-driver-595 && sudo reboot" ;;
  esac
  echo
  echo "  برای دیدن روند بوت:"
  echo "    sudo dmesg | grep -iE 'nvidia|nvrm' | head -30"
  return 2
}

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
MODULES_DIR="${MODULES_DIR:-/lib/modules}"
PKGS="$(dpkg -l 2>/dev/null | awk '/^ii +nvidia-driver-[0-9]/{print $2}' | tr '\n' ' ')"
echo "  drivers     : ${PKGS:-none}"
DKMS_N="$(dkms status 2>/dev/null | grep -c nvidia || true)"
echo "  dkms entries: ${DKMS_N}"

if (( NOUVEAU_OFF )); then free_nouveau; exit $?; fi
if (( PCI_RESET )); then pci_reset_gpu; exit $?; fi

if (( DEEP )); then
  deep_diag
  exit $?
fi

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

SIGNER="$(modinfo -F signer nvidia 2>/dev/null || true)"
VERMAGIC="$(modinfo -F vermagic nvidia 2>/dev/null | awk '{print $1}' || true)"
echo "  signer of this kernel's module: ${SIGNER:-none}${VERMAGIC:+ (built for $VERMAGIC)}"
OTHER_SIGNED=""
for d in "$MODULES_DIR"/*/; do
  k="$(basename "$d")"
  [[ "$k" == "$KERNEL" ]] && continue
  sgn="$(modinfo -k "$k" -F signer nvidia 2>/dev/null || true)"
  case "$sgn" in
    *Canonical*) OTHER_SIGNED="$k"; echo "  kernel $k دارد ماژول امضاشدهٔ Canonical" ;;
    "") ;;
    *) echo "  kernel $k دارد ماژول با امضای: $sgn" ;;
  esac
done

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
  local vram_mib
  vram_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -dc '0-9')"
  if [[ -n "$vram_mib" ]] && (( vram_mib > 0 && vram_mib < 8192 )); then
    if unit_add_env OLLAMA_CONTEXT_LENGTH 8192; then
      ok "VRAM ≈ ${vram_mib} MiB: زمینهٔ مدل روی ۸۱۹۲ گذاشته شد تا کامل در کارت جا شود"
    else
      warn "VRAM ≈ ${vram_mib} MiB: در سرویس ollama این را بگذار: Environment=OLLAMA_CONTEXT_LENGTH=8192"
    fi
  fi
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

if (( MODULE_OK == 0 )) && [[ -n "$OTHER_SIGNED" ]]; then
  step "4/5 فقط باید به کرنل $OTHER_SIGNED بوت کنی"
  ok "ماژول NVIDIA امضاشدهٔ Canonical برای کرنل $OTHER_SIGNED نصب است،"
  ok "ولی تو روی $KERNEL هستی. Secure Boot این امضا را قبول می‌کند."
  echo "      sudo reboot      # GRUB پیش‌فرض روی جدیدترین کرنل است"
  echo "      # بعد از بالا آمدن: uname -r   (باید $OTHER_SIGNED باشد)"
  echo "      ./scripts/fix_nvidia.sh --check && ./scripts/setup_llm.sh --hardware"
  echo
  dim "اگر GRUB کرنل قدیمی را بالا آورد: در منوی GRUB «Advanced options» و همان $OTHER_SIGNED را انتخاب کن"
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
