PYTHON ?= python3
NPM ?= npm
ENV_FILE ?= .env
RADAR_HOST ?= 127.0.0.1
RADAR_PORT ?= 5000
RADAR_HOME ?= $(HOME)/.radar
RADAR_WORK_DIR ?= debug/work
RADAR_ACTOR_PATH ?= builtin/actor
RADAR_COLLECTOR_VIEWER_HOST ?= 127.0.0.1
RADAR_COLLECTOR_VIEWER_PORT ?= 5174
RADAR_COLLECTOR_VIEWER_DATA ?= $(RADAR_WORK_DIR)/collectors
RADAR_DESKTOP_DIR ?= desktop
SEATALK_THREAD_ICON_RIGHT ?= 239
SEATALK_THREAD_ICON_TOP ?= 31
SEATALK_THREAD_ROW_RIGHT ?= 320
SEATALK_THREAD_ROW_TOP ?= 160
SEATALK_THREAD_OPEN_DELAY ?= 0.8

ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif

.PHONY: install install-desktop run-coordinator run-collector-viewer run-desktop package-chrome-extension actor-live-setup seatalk-ping-actor-live seatalk-ping-actor-open seatalk-ping-applescript-open test test-unit test-sample-collector test-chat-transcript-collector test-chat-skill-processor test-seatalk-collector test-chrome-collector test-macos-collector test-macos-collector-unit test-actor-runtime test-actor-live test-coordinator-actor-api axtree-debug

# Install Python dependencies used by the debug harness and collectors.
install:
	$(PYTHON) -m pip install -r requirements.txt

# Install dependencies for the Tauri desktop app and its frontend.
install-desktop:
	$(NPM) --prefix $(RADAR_DESKTOP_DIR) install
	$(NPM) --prefix $(RADAR_DESKTOP_DIR)/frontend install

# Run the local Socket.IO coordinator/debug harness.
run-coordinator:
	$(PYTHON) debug/app.py --host $(RADAR_HOST) --port $(RADAR_PORT) --work-dir $(RADAR_WORK_DIR) --actor-path $(RADAR_ACTOR_PATH) $(if $(RADAR_COLLECTOR_META),--collector-meta $(RADAR_COLLECTOR_META),)

# Run the local collector JSONL debug viewer.
run-collector-viewer:
	cd debug/collector_viewer && npm start -- --host $(RADAR_COLLECTOR_VIEWER_HOST) --port $(RADAR_COLLECTOR_VIEWER_PORT) --data $(abspath $(RADAR_COLLECTOR_VIEWER_DATA))

# Print a target app's macOS Accessibility tree for actor development.
axtree-debug:
	$(PYTHON) debug/axtree_debugger.py --depth $(or $(RADAR_AXTREE_DEPTH),5) $(if $(RADAR_AXTREE_APP),--app "$(RADAR_AXTREE_APP)",) $(if $(RADAR_AXTREE_BUNDLE_ID),--bundle-id "$(RADAR_AXTREE_BUNDLE_ID)",)

# Run the Radar Tauri desktop app in development mode.
run-desktop:
	$(NPM) --prefix $(RADAR_DESKTOP_DIR) run tauri:dev

# Package Radar's Chrome extension into dist/radar-extension.
package-chrome-extension:
	$(PYTHON) -m extensions.package_radar_chrome

# Print manual setup steps for the live actor browser test.
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

# Run the SeaTalk @You thread actor live. Keep this process running, then click
# the Radar conversation row in SeaTalk; the macOS click collector routes that
# click event to the automatic actor.
seatalk-ping-actor-live:
	@echo "Starting Radar with the macOS click collector."
	@echo "When SeaTalk opens, click the Radar conversation row that shows @You."
	@echo "Stop this process with Ctrl-C after the actor opens the thread."
	open -a SeaTalk
	sleep 1
	$(PYTHON) debug/seatalk_axtree_viewer.py --scope rows
	@echo ""
	@echo "AX snapshot printed above. Now click the Radar conversation row in SeaTalk."
	SEATALK_THREAD_ICON_RIGHT=$(SEATALK_THREAD_ICON_RIGHT) \
	SEATALK_THREAD_ICON_TOP=$(SEATALK_THREAD_ICON_TOP) \
	SEATALK_THREAD_ROW_RIGHT=$(SEATALK_THREAD_ROW_RIGHT) \
	SEATALK_THREAD_ROW_TOP=$(SEATALK_THREAD_ROW_TOP) \
	SEATALK_THREAD_OPEN_DELAY=$(SEATALK_THREAD_OPEN_DELAY) \
	RADAR_DISABLE_CHROME_BRIDGE=1 RADAR_MACOS_PROMPT_PERMISSIONS=1 $(PYTHON) debug/app.py --host $(RADAR_HOST) --port $(RADAR_PORT) --work-dir $(RADAR_WORK_DIR) --actor-path $(RADAR_ACTOR_PATH) --collector-meta builtin/collector/macos/meta.json --disable-chrome-bridge

# Direct fallback: open the Radar @You thread immediately without waiting for a
# click event.
seatalk-ping-actor-open:
	open -a SeaTalk
	SEATALK_THREAD_ICON_RIGHT=$(SEATALK_THREAD_ICON_RIGHT) \
	SEATALK_THREAD_ICON_TOP=$(SEATALK_THREAD_ICON_TOP) \
	SEATALK_THREAD_ROW_RIGHT=$(SEATALK_THREAD_ROW_RIGHT) \
	SEATALK_THREAD_ROW_TOP=$(SEATALK_THREAD_ROW_TOP) \
	SEATALK_THREAD_OPEN_DELAY=$(SEATALK_THREAD_OPEN_DELAY) \
	$(PYTHON) builtin/actor/seatalk_thread_ping/action.py

# Standalone AppleScript fallback for SeaTalk: click the thread icon, then click
# the visible @You thread row in the drawer. Override the offsets if your
# SeaTalk window layout differs.
seatalk-ping-applescript-open:
	@echo "Opening SeaTalk thread drawer via AppleScript coordinate clicks."
	@echo "Offsets: icon right=$(SEATALK_THREAD_ICON_RIGHT), icon top=$(SEATALK_THREAD_ICON_TOP), row right=$(SEATALK_THREAD_ROW_RIGHT), row top=$(SEATALK_THREAD_ROW_TOP)"
	swiftc builtin/actor/seatalk_thread_ping/seatalk_ping_helper.swift -framework AppKit -framework ApplicationServices -framework CoreGraphics -o /private/tmp/radar_seatalk_ping_helper
	SEATALK_THREAD_ICON_RIGHT=$(SEATALK_THREAD_ICON_RIGHT) \
	SEATALK_THREAD_ICON_TOP=$(SEATALK_THREAD_ICON_TOP) \
	SEATALK_THREAD_ROW_RIGHT=$(SEATALK_THREAD_ROW_RIGHT) \
	SEATALK_THREAD_ROW_TOP=$(SEATALK_THREAD_ROW_TOP) \
	SEATALK_THREAD_OPEN_DELAY=$(SEATALK_THREAD_OPEN_DELAY) \
	SEATALK_CLICK_HELPER=/private/tmp/radar_seatalk_ping_helper \
	osascript debug/seatalk_click_ping_thread.applescript

# Run the full test suite, including live actor checks.
test: test-unit test-actor-live

# Run the non-live unit and integration tests.
test-unit: test-sample-collector test-chat-transcript-collector test-chat-skill-processor test-seatalk-collector test-chrome-collector test-macos-collector-unit test-actor-runtime

# Run the sample collector integration test.
test-sample-collector:
	$(PYTHON) -m py_compile debug/app.py builtin/collector/sample/collector.py debug/test_sample_collector.py
	RADAR_DISABLE_CHROME_BRIDGE=1 $(PYTHON) debug/test_sample_collector.py

# Run the Codex/Claude chat transcript collector fixture test.
test-chat-transcript-collector:
	$(PYTHON) -m py_compile builtin/collector/chat_transcript/collector.py debug/test_chat_transcript_collector.py
	$(PYTHON) debug/test_chat_transcript_collector.py

# Run the transcript-to-skill processor fixture test.
test-chat-skill-processor:
	$(PYTHON) -m py_compile builtin/collector/chat_transcript/collector.py builtin/processor/chat_skill/processor.py debug/test_chat_skill_processor.py
	$(PYTHON) debug/test_chat_skill_processor.py

# Run the SeaTalk user-authored-message collector fixture test.
test-seatalk-collector:
	$(PYTHON) -m py_compile builtin/collector/seatalk/collector.py debug/test_seatalk_collector.py
	$(PYTHON) debug/test_seatalk_collector.py

# Run the Chrome browser collector unit tests.
test-chrome-collector:
	$(PYTHON) -m py_compile builtin/collector/chrome/runtime.py builtin/collector/chrome/observation.py tests/test_chrome_collector.py
	$(PYTHON) -m unittest tests.test_chrome_collector

# Run the macOS activity collector against the debug coordinator until stopped.
test-macos-collector:
	RADAR_COLLECTOR_META=builtin/collector/macos/meta.json $(PYTHON) debug/app.py --host $(RADAR_HOST) --port $(RADAR_PORT) --work-dir $(RADAR_HOME) --actor-path $(RADAR_ACTOR_PATH)

# Run the macOS activity collector unit tests.
test-macos-collector-unit:
	$(PYTHON) -m py_compile builtin/collector/macos/runtime.py builtin/collector/macos/observation.py tests/test_macos_collector.py
	$(PYTHON) -m unittest tests.test_macos_collector

# Run actor runtime tests with local debug context and Chrome bridge stubs.
test-actor-runtime:
	$(PYTHON) -m py_compile debug/app.py debug/active_context.py debug/chrome_bridge.py debug/actor_runtime.py debug/test_actor_runtime.py builtin/actor/youtube_search/should_trigger.py builtin/actor/youtube_search/action.py builtin/actor/gmail_followup_draft/should_trigger.py builtin/actor/gmail_followup_draft/action.py builtin/actor/gmail_reply_email/should_trigger.py builtin/actor/gmail_reply_email/action.py builtin/actor/calendar_next_open_timeslot/should_trigger.py builtin/actor/calendar_next_open_timeslot/action.py builtin/actor/codex_skill/lib.py builtin/actor/codex_skill/should_trigger.py builtin/actor/codex_skill/action.py
	$(PYTHON) debug/test_actor_runtime.py

# Run coordinator API tests for actor registration and routing.
test-coordinator-actor-api:
	$(PYTHON) -m py_compile debug/app.py debug/test_coordinator_actor_api.py
	$(PYTHON) debug/test_coordinator_actor_api.py

# Run the live actor test after verifying coordinator actor APIs.
test-actor-live: test-coordinator-actor-api
