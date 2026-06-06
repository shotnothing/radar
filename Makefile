PYTHON ?= python3
ENV_FILE ?= .env
RADAR_HOST ?= 127.0.0.1
RADAR_PORT ?= 5000
RADAR_WORK_DIR ?= debug/work
RADAR_ACTOR_PATH ?= builtin/actor

ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif

.PHONY: install run-coordinator package-chrome-extension actor-live-setup test test-unit test-sample-collector test-chat-transcript-collector test-seatalk-collector test-chrome-collector test-actor-runtime test-actor-live test-coordinator-actor-api

install:
	$(PYTHON) -m pip install -r requirements.txt

run-coordinator:
	$(PYTHON) debug/app.py --host $(RADAR_HOST) --port $(RADAR_PORT) --work-dir $(RADAR_WORK_DIR) --actor-path $(RADAR_ACTOR_PATH) $(if $(RADAR_COLLECTOR_META),--collector-meta $(RADAR_COLLECTOR_META),)

package-chrome-extension:
	$(PYTHON) -m extensions.package_radar_chrome

actor-live-setup:
	@echo "Live actor test setup:"
	@echo "1. Package Radar's Chrome extension:"
	@echo "   make package-chrome-extension"
	@echo "2. Open Chrome extensions:"
	@echo "   open -a 'Google Chrome' chrome://extensions"
	@echo "3. Enable Developer mode and Load unpacked:"
	@echo "   $(CURDIR)/dist/radar-extension"
	@echo "4. Open YouTube in Chrome:"
	@echo "   open -a 'Google Chrome' https://www.youtube.com/"
	@echo "5. Run:"
	@echo "   make test-actor-live"

test: test-unit test-actor-live

test-unit: test-sample-collector test-chat-transcript-collector test-seatalk-collector test-chrome-collector test-actor-runtime

test-sample-collector:
	$(PYTHON) -m py_compile debug/app.py builtin/collector/sample/collector.py debug/test_sample_collector.py
	RADAR_DISABLE_CHROME_BRIDGE=1 $(PYTHON) debug/test_sample_collector.py

test-chat-transcript-collector:
	$(PYTHON) -m py_compile builtin/collector/chat_transcript/collector.py debug/test_chat_transcript_collector.py
	$(PYTHON) debug/test_chat_transcript_collector.py

test-seatalk-collector:
	$(PYTHON) -m py_compile builtin/collector/seatalk/collector.py debug/test_seatalk_collector.py
	$(PYTHON) debug/test_seatalk_collector.py

test-chrome-collector:
	$(PYTHON) -m py_compile builtin/collector/chrome/runtime.py builtin/collector/chrome/observation.py tests/test_chrome_collector.py
	$(PYTHON) -m unittest tests.test_chrome_collector

test-actor-runtime:
	$(PYTHON) -m py_compile debug/app.py debug/active_context.py debug/chrome_bridge.py debug/actor_runtime.py debug/test_actor_runtime.py builtin/actor/youtube_search/should_trigger.py builtin/actor/youtube_search/action.py
	$(PYTHON) debug/test_actor_runtime.py

test-coordinator-actor-api:
	$(PYTHON) -m py_compile debug/app.py debug/test_coordinator_actor_api.py
	$(PYTHON) debug/test_coordinator_actor_api.py

test-actor-live: test-coordinator-actor-api
