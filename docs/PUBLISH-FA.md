# انتشار گزارش‌ها در گیت

## چه چیزی منتشر می‌شود

فقط پوشه‌ی گزارش (`reports/paper/...`) روی شاخه‌ی `reports`:

```
reports/paper/latest.md          گزارش کامل آخرین چرخه
reports/paper/cycles.jsonl       یک خط JSON به ازای هر چرخه (حداکثر ۵۰۰۰ خط)
reports/paper/daily/2026-11-26.md
reports/paper/policy.json        سیاست فعال + سیاست چرخه‌ی بعد
reports/paper/state.json         بالانس/اکوییتی/استریک/آمار
reports/paper/README.md          توضیح قالب
```

ریپوی کد (`main` یا هر شاخه‌ی دیگر) **دست نمی‌خورد**: انتشار در کلون جداگانه‌ی
`var/publish` انجام می‌شود و فقط همان شاخه پوش می‌شود.

## راه‌اندازی

```bash
.venv/bin/tradeagent publish-test          # ساخت/بررسی کلون، بدون کامیت
.venv/bin/tradeagent publish-test --write  # یک کامیت واقعی برای تست احراز هویت
```

اگر کلون ساخته شد ولی پوش خطا داد، مشکل احراز هویت است. دو گزینه:

### الف) HTTPS + توکن (ساده‌تر)

```bash
# توکن با دسترسی repo (fine-grained: Contents = Read and write)
git config --global credential.helper store
# یک بار push دستی بزن و توکن را به‌عنوان password وارد کن
```

یا با `gh`:

```bash
gh auth login          # سپس gh auth setup-git
```

### ب) SSH

```bash
ssh-keygen -t ed25519 -C tradeagent -N '' -f ~/.ssh/tradeagent
cat ~/.ssh/tradeagent.pub     # به GitHub → Settings → SSH keys اضافه کن
# تست:
GIT_SSH_COMMAND='ssh -i ~/.ssh/tradeagent' git ls-remote git@github.com:USER/REPO.git
```

بعد در `configs/local.toml`:

```toml
[publish]
enabled = true
remote = "git@github.com:enigmafarsco-lang/tradeAgent.git"   # یا خالی = origin
branch = "reports"
mode = "every_cycle"
```

## رفتار در خطا

| وضعیت | نتیجه |
|---|---|
| کلون اولیه نیست | شاخه‌ی `reports` orphan ساخته می‌شود |
| `reports` روی remote نیست | شاخه‌ی تازه ساخته و پوش می‌شود |
| هیچ تغییری نبود | `no-changes` — کامیت اضافه نمی‌سازد |
| پوش رد شد | یک بار `pull --rebase --autostash` و تلاش دوباره |
| شبکه/توکن خراب | کامیت **محلی می‌ماند** (`committed-local`) + هشدار در گزارش |
| سقف `max_commits_per_hour` | کامیت می‌شود ولی پوش به چرخه‌ی بعد می‌افتد |

هیچ‌وقت تاریخچه بازنویسی نمی‌شود (`--force` وجود ندارد) و ریپوی کد لمس نمی‌شود.

## حجم

هر چرخه حدود ۵–۱۵ کیلوبایت تغییر (`latest.md` بازنویسی + یک خط jsonl + daily).
روزی ۲۸۸ کامیت (هر ۵ دقیقه) — اگر تاریخچه‌ات را شلوغ می‌دانی:

```toml
[publish]
mode = "daily"          # فقط یک کامیت در روز (جمع‌شده)
max_commits_per_hour = 6
```

## خواندن از سمت ایجنت

ایجنت (من) با `git`/GitHub API همین شاخه را می‌خواند. مهم‌ترین فایل:

```bash
gh api repos/enigmafarsco-lang/tradeAgent/contents/reports/paper/latest.md?ref=reports \
  -H "Accept: application/vnd.github.raw" | head -60
```

یا کل خط‌های تاریخ:

```bash
gh api repos/.../contents/reports/paper/cycles.jsonl?ref=reports -H "Accept: application/vnd.github.raw" \
  | tail -20 | jq -c '{cycle,ts,pnl_pct,variant,level,trades_closed_cycle}'
```

## اگر نمی‌خواهی گزارش‌ها در گیت عمومی باشند

گزینه‌ها:

1. ریپوی خصوصی جدا فقط برای گزارش (`publish.remote` را روی آن بگذار).
2. `publish.enabled = false` و فقط گزارش محلی در `var/reports/latest.md`
   (بعداً با `scp`/`rsync` منتقل کن).
3. یک گیت‌سرور محلی (Gitea/Forgejo) روی همان کامپیوتر.
