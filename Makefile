PYTHON ?= python3
ENV_FILE ?= .env

ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif

.PHONY: install run-coordinator package-chrome-extension test-sample-collector test-chat-transcript-collector

install:
	$(PYTHON) -m pip install -r requirements.txt

run-coordinator:
	$(PYTHON) debug/app.py --host $(RADAR_HOST) --port $(RADAR_PORT) --collector-meta $(RADAR_COLLECTOR_META)

package-chrome-extension:
	$(PYTHON) -m builtin.collector.chrome.package_extension

test-sample-collector:
	$(PYTHON) -m py_compile debug/app.py builtin/collector/sample/collector.py debug/test_sample_collector.py
	$(PYTHON) debug/test_sample_collector.py

test-chat-transcript-collector:
	$(PYTHON) -m py_compile builtin/collector/chat_transcript/collector.py debug/test_chat_transcript_collector.py
	$(PYTHON) debug/test_chat_transcript_collector.py
