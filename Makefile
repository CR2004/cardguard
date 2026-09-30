# CardGuard demo and checks. One command each; see scripts/demo.py for what the demo targets start.
#
#   make demo          bank + merchant (in-process decisions)       -> http://127.0.0.1:4242
#   make demo-flower   the same through a local Flower SuperLink + SuperNode
#   make test          Python tests, then web typecheck, lint, tests and build (runs every step, fails if any failed)
#   make build         the web UI (web/dist), rebuilt only when its sources changed
#   make stop          stop a demo left running (only this demo's own processes)
#   make clean         remove the web build and the demo's runtime files (keeps .demo labels, audit and keys)
#
# Stripe TEST keys: .env, or ~/credentials/stripe_test.txt (SECRET_API_KEY / PUBLISHABLE_KEY; another folder with
# CARDGUARD_CREDENTIALS=...). Jev and Flower keys are picked up the same way when present. Values are never printed.

PY     ?= .venv/bin/python
PNPM   ?= pnpm
STORES ?= store-a,store-b,store-c

WEB_SRC := $(shell find web/src -type f) web/index.html web/package.json web/tsconfig.json web/vite.config.ts

.PHONY: demo demo-flower test build stop clean

demo: build $(PY)
	$(PY) scripts/demo.py $(if $(STORES),--stores $(STORES))

demo-flower: build $(PY)
	$(PY) scripts/demo.py --flower $(if $(STORES),--stores $(STORES))

build: web/dist/index.html

web/dist/index.html: web/node_modules/.modules.yaml $(WEB_SRC)
	$(PNPM) --dir web build

web/node_modules/.modules.yaml: web/package.json web/pnpm-lock.yaml
	$(PNPM) --dir web install --frozen-lockfile
	@touch $@

$(PY):
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements.txt

test: $(PY) web/node_modules/.modules.yaml
	@status=0; \
	$(PY) -m pytest -q || status=1; \
	$(PNPM) --dir web typecheck || status=1; \
	$(PNPM) --dir web lint || status=1; \
	$(PNPM) --dir web test || status=1; \
	$(PNPM) --dir web build || status=1; \
	if [ $$status -ne 0 ]; then echo "make test: at least one step failed (see above)"; fi; \
	exit $$status

stop: $(PY)
	$(PY) scripts/demo.py --stop

clean:
	rm -rf web/dist .demo/run
