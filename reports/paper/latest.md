# گزارش چرخه‌ی کاغذی — 2026-11-27T15:55:54Z

> چرخه **#216** · منبع داده: `harvester` · حالت: **کاغذی** (هیچ سفارش واقعی ارسال نمی‌شود) · نسخه 0.1.0

## ۱) خلاصه

| شاخص | مقدار |
|---|---|
| بالانس | 9,290.29 |
| اکوییتی (با سود/زیان باز) | 9,339.57 |
| سود/زیان از شروع | -660.43 (-6.60%) |
| افت از اوج | 6.60% |
| معاملات کل | 33 (برد 7 / باخت 26) |
| وین‌ریت کل | 21.21% |
| میانگین R | -0.425 |
| استریک فعلی | 0 برد / 3 باخت |
| کندل پردازش‌شده این چرخه | 231 |
| معاملات بسته‌شده این چرخه | 0 |
| قفل ورود | سقف افت روزانه فعال است |

## ۲) معاملات بسته‌شده در این چرخه

_این چرخه معامله‌ای بسته نشد._

## ۳) پوزیشن‌های باز

| نماد | جهت | ورود | قیمت فعلی | سود باز | SL | TP | سن |
|---|---|---|---|---|---|---|---|
| XAUUSD_i | خرید | 3621.29 | 3628.38 | +49.28 | 3614.61 | 3630.91 | 0s |

## ۴) سیاست معاملاتی

**سیاست فعال این چرخه:** شکست تکانه | پله 3 | SL=1.495×ATR، RR=1.44، نگهداری=810s، اسپرد≤4.5bp، ریسک×1.0

- نسخه: `13` · منبع: `adaptive` · پله: `3`
- دلیل آخرین تغییر: سخت‌ترکردن شرط ورود (breakout_k ↑، حداقل لبه ↑) • فیلتر هزینه سخت‌تر (سقف اسپرد ↓، فاصله بین ورودها ↑) • فاصله‌ی استاپ ↑ و هدف نزدیک‌تر (کاهش نویز-استاپ)

**تغییرات اعمال‌شده برای چرخه‌ی بعد:**
- **نردبان قطعی** · پله 3 → 4 · 3 ضرر پشت‌سرهم → پله 4 — سخت‌ترکردن شرط ورود (breakout_k ↑، حداقل لبه ↑) • فیلتر هزینه سخت‌تر (سقف اسپرد ↓، فاصله بین ورودها ↑) • فاصله‌ی استاپ ↑ و هدف نزدیک‌تر (کاهش نویز-استاپ) • تغییر خانواده‌ی استراتژی
  - استراتژی: `momentum` → `meanrev`
  - `breakout_k`: 1.25 → 1.0
  - `cooldown_s`: 450 → 240
  - `hold_s`: 810 → 600
  - `max_spread_bps`: 4.5 → 5.0
  - `max_vol_bps`: 90.0 → 35.0
  - `min_edge_bps`: 3.0 → 1.5
  - `rr`: 1.44 → 1.2
  - `sl_atr`: 1.495 → 1.2

**سیاست چرخه‌ی بعد:** بازگشت به میانگین | پله 4 | SL=1.2×ATR، RR=1.2، نگهداری=600s، اسپرد≤5.0bp، ریسک×1.0

## ۵) کیفیت داده

| نماد | کندل تازه | آخرین تیک (UTC) | تأخیر | وضعیت |
|---|---|---|---|---|
| XAUUSD_i | 232 | 2026-11-27T17:58:35Z | 0s | سالم |

**روند از نگاه ایجنت (بر پایهٔ حرکت ثانیه‌ای):**

- جهت: **up** · فاز: `accelerating` · قدرت: 0.75 · افق: 300s · اطمینان: 0.80
- استدلال مدل: نوسانات 15 ثانیه‌ای افزایش یافته‌اند.
- کارنامهٔ پیش‌بینی‌های ایجنت: 41% درست (34 پیش‌بینی نمره‌داده‌شده، وزنی 42%، در انتظار 1)

_خلاصهٔ عددی حرکت به مدل داده شد: بازده ۵/۱۵/۳۰/۶۰ ثانیه، تغییر سرعت، سهم تیک صعودی و اسپرد (نمونه: XAUUSD_i ۱۵s=2.18bp)._

## ۶) مدل زبانی

- مسیر: **ایجنت ابزاردار (MCP)** · دور گفت‌وگو: 3
- ابزارهای صدا زده‌شده:
  - `local_tick_digest` ✓ · 38ms
  - `local_candles` ✓ · 20ms
  - `local_candles` ✓ · 18ms
  - `local_candles` ✓ · 25ms
  - `MovingAverage` ✗ · 0ms · ابزار ناشناخته؛ فقط این نام‌ها وجود دارند: account_info, analyze_market, cancel_proposal, confirm_proposal, find_symbol, get_candles, journal_add, journal_list, list_positions, list_proposals, local_candles, local_tick_digest, market_snapshot, mt5_status, position_size, propose_manage_position, propose_order, risk_report, symbol_spec, trade_history
  - `BollingerBands` ✗ · 0ms · ابزار ناشناخته؛ فقط این نام‌ها وجود دارند: account_info, analyze_market, cancel_proposal, confirm_proposal, find_symbol, get_candles, journal_add, journal_list, list_positions, list_proposals, local_candles, local_tick_digest, market_snapshot, mt5_status, position_size, propose_manage_position, propose_order, risk_report, symbol_spec, trade_history
- تحلیل مدل: price_action: The price action shows a range-bound movement with some volatility. The price oscillates between 3624.09 and 3626.28 within the given timeframe. There are several instances of quick price movements, indicating potential short-term trading opportunities.؛ volume: The volume is consistent with the number of ticks, suggesting that the market is actively traded but not excessively so. Th
- نتیجه: **رد شد: کلیدهای نادیده‌گرفته‌شده: ['entry_points', 'exit_points', 'risk_management', 'strategy']؛ پیشنهاد هیچ تغییری نداشت — رد شد**

## ۷) هشدارها

_هشداری نیست._

## ۸) برای ایجنت — بررسی چرخه‌ی بعد

- [ ] افت از اوج 6.60% است؛ آیا نردبان سازگاری درست عمل کرده؟
- [ ] آیا نتیجه‌ی این چرخه با انتظار آماری سیاست هم‌خوان است؟
- [ ] تغییر سیاست این چرخه را با داده‌ی بعدی بسنج؛ اگر نتیجه بدتر شد، پله را برگردان.
- [ ] پیشنهاد مدل زبانی را با آمار واقعی مقایسه کن (پذیرش/رد در بخش ۶ آمده است).
- [ ] اگر لازم بود، پارامترهای `configs/local.toml` یا سیاست را با کامیت بعدی اصلاح کن.

---
_این گزارش خودکار توسط TradeAgent ساخته شد. کاغذی و پژوهشی است، توصیه‌ی مالی نیست. زمان تولید: 2026-11-27T16:00:05Z._
