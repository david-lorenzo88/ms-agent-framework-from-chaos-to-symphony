# From Chaos to Symphony - Baltic Summit 2026
.PHONY: help install demo devui web smoke drive deck deck-qa deploy lint clean

PY := .venv/bin/python

help:
	@echo "make install   create the venv and install everything"
	@echo "make demo      run DevUI (:8080) and the showcase site (:8000)"
	@echo "make devui     DevUI only"
	@echo "make web       showcase site only"
	@echo "make smoke     run all twelve patterns end to end"
	@echo "make drive     drive all twelve through the browser UI (needs make demo running)"
	@echo "make deck      rebuild the session deck from deck/content.py"
	@echo "make deck-qa   check the deck's text fits its boxes"
	@echo "make deploy    publish to Azure Container Apps (needs az login)"

# uv if it is on PATH, otherwise stdlib venv + pip. The first command someone
# types after cloning should not fail on a tool they have never heard of.
install:
	@if command -v uv >/dev/null 2>&1; then \
		echo "using uv"; \
		uv venv --python 3.12 .venv && . .venv/bin/activate && uv pip install -e ".[dev]"; \
	else \
		echo "uv not found, using python -m venv"; \
		python3 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip -q && pip install -e ".[dev]"; \
	fi
	@echo ""
	@echo "Done. Run: make demo"

demo:
	@echo "DevUI    -> http://localhost:8080"
	@echo "Showcase -> http://localhost:8000"
	@$(PY) -m chaos_to_symphony.devui_app & \
	 $(PY) -m chaos_to_symphony.api; \
	 kill %1 2>/dev/null || true

devui:
	$(PY) -m chaos_to_symphony.devui_app

web:
	$(PY) -m chaos_to_symphony.api

smoke:
	$(PY) scripts/smoke.py

# Needs both servers up in another terminal, plus:
#   .venv/bin/pip install playwright && .venv/bin/playwright install chromium
drive:
	$(PY) scripts/drive.py

deck:
	$(PY) deck/build_deck.py

deck-qa:
	$(PY) deck/qa_deck.py

deploy:
	./infra/deploy.sh

lint:
	. .venv/bin/activate && ruff check src scripts deck

clean:
	rm -rf deck/build .ruff_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
