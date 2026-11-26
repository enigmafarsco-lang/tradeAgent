SHELL := /bin/bash
VENV  := .venv
PY    := $(VENV)/bin/python
TA    := $(VENV)/bin/tradeagent

.PHONY: help install test demo doctor cycle loop status report mt5 mt5-find mt5-compile clean

help:
	@echo "make install      نصب venv و بسته"
	@echo "make test         تست‌های آفلاین"
	@echo "make demo         اجرای انتها-به-انتها با داده‌ی ساختگی"
	@echo "make doctor       بررسی سلامت داده/انتشار/مدل"
	@echo "make cycle        یک چرخه‌ی واقعی (بدون انتشار)"
	@echo "make loop         حلقه‌ی ۵ دقیقه‌ای"
	@echo "make status       وضعیت فعلی از ژورنال"
	@echo "make report       مسیر آخرین گزارش (یا make report-cat)"
	@echo "make mt5          نصب MT5 روی Wine (dry-run اول)"
	@echo "make mt5-find     پیدا کردن پوشه‌ی MQL5/Files"
	@echo "make mt5-compile  کامپایل اکسپرت با MetaEditor"

install:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -U pip setuptools wheel
	$(VENV)/bin/pip install -q -e ".[dev]"

test: install
	$(PY) -m pytest -q

demo: install
	$(TA) demo --cycles 6 --step 300

doctor: install
	$(TA) doctor

cycle: install
	$(TA) cycle --no-publish

loop: install
	$(TA) loop --interval 300

status: install
	$(TA) status

report: install
	$(TA) report

report-cat: install
	$(TA) report --cat

mt5:
	./scripts/install_mt5_wine.sh --dry-run
	@echo "برای نصب واقعی: ./scripts/install_mt5_wine.sh"

mt5-find:
	./scripts/find_mt5_data.sh

mt5-compile:
	./scripts/compile_harvester.sh

clean:
	rm -rf var/reports var/logs var/*.sqlite3 var/*.lock var/ingest_state.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
