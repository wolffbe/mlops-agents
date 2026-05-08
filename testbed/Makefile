.PHONY: install prepare-task start stop task task-test task-xs task-s \
        interface-build interface-install interface-uninstall interface-test \
        clean reset reset-all

# Defaults for `make task`. Override on the command line:
#   make task TASK=titanic                   # any Kaggle competition slug
#   make task INTERFACE=local,sdk            # comma-separated list
#   make task INTERFACE=all                  # all four interfaces
# `make prepare-task TASK=<slug>` must be run first so MLE-bench has the data.
TASK       ?= aerial-cactus-identification
MODEL      ?= claude-sonnet
INTERFACE  ?= local
SKILLS     ?=
REPS       ?= 1      # number of replicates per (interface × task) — for error bars
PROJECT    ?= hops   # project name; agent invocations matching this token are
                     # classified as project CLI/SDK/MCP calls vs. vanilla shell/python

# Resolve INTERFACE into a space-separated list of interfaces to run.
empty :=
space := $(empty) $(empty)
comma := ,
ifeq ($(INTERFACE),all)
INTERFACES := local sdk cli mcp
else
INTERFACES := $(subst $(comma),$(space),$(INTERFACE))
endif

# The 8 MLE-bench competitions selected for the experiment, in the order they
# appear in the thesis. Used by `make run-xs`. Sizes (GB) noted for context.
#   aerial-cactus-identification               0.025  (image classification)
#   denoising-dirty-documents                  0.060  (image-to-image)
#   detecting-insults-in-social-commentary     0.002  (text classification)
#   histopathologic-cancer-detection           7.760  (image regression)
#   mlsp-2013-birds                            0.585  (audio classification)
#   nomad2018-predict-transparent-conductors   0.006  (tabular)
#   tabular-playground-series-dec-2021         0.700  (tabular)
#   text-normalization-challenge-english-...   0.010  (sequence-to-sequence)
TASKS_XS = \
    aerial-cactus-identification \
    denoising-dirty-documents \
    jigsaw-toxic-comment-classification-challenge \
    histopathologic-cancer-detection \
    mlsp-2013-birds \
    nomad2018-predict-transparent-conductors \
    tabular-playground-series-dec-2021 \
    text-normalization-challenge-english-language

# MLE-bench's "Low complexity" / Lite split (22 competitions, ~158GB total).
# Source: https://github.com/openai/mle-bench/blob/main/experiments/splits/low.txt
TASKS_S = \
    aerial-cactus-identification \
    aptos2019-blindness-detection \
    denoising-dirty-documents \
    detecting-insults-in-social-commentary \
    dog-breed-identification \
    dogs-vs-cats-redux-kernels-edition \
    histopathologic-cancer-detection \
    jigsaw-toxic-comment-classification-challenge \
    leaf-classification \
    mlsp-2013-birds \
    new-york-city-taxi-fare-prediction \
    nomad2018-predict-transparent-conductors \
    plant-pathology-2020-fgvc7 \
    random-acts-of-pizza \
    ranzcr-clip-catheter-line-classification \
    siim-isic-melanoma-classification \
    spooky-author-identification \
    tabular-playground-series-dec-2021 \
    tabular-playground-series-may-2022 \
    text-normalization-challenge-english-language \
    text-normalization-challenge-russian-language \
    the-icml-2013-whale-challenge-right-whale-redux

RUN_FLAGS = --model $(MODEL) --reps $(REPS) \
            $(if $(SKILLS),--skills $(SKILLS)) \
            $(if $(PROJECT),--project $(PROJECT))

CELL_FLAGS = --model $(MODEL) $(if $(SKILLS),--skills $(SKILLS))

install:
	bash scripts/install.sh

prepare-task:
	uv run prepare-task $(TASK)

start:
	bash scripts/start.sh

stop:
	bash scripts/stop.sh

# Each invocation upserts one (model, interface, task, skill, rep) row in
# results.csv. rep is auto-assigned by the coordinator (lowest free slot in
# 0..REPS-1, else FIFO eviction of the oldest matching row). Looping REPS
# times per (interface × task) fills the slots; running again overwrites the
# oldest slot first.

task:
	@for i in $(INTERFACES); do \
		echo "=== interface=$$i task=$(TASK) reps=$(REPS) ==="; \
		for n in $$(seq 1 $(REPS)); do \
			uv run coordinator --task $(TASK) --interface $$i $(RUN_FLAGS) \
				|| echo "errored, continuing"; \
		done; \
		[ "$$i" = "local" ] || uv run cell-feedback --task $(TASK) --interface $$i $(CELL_FLAGS) || true; \
	done

# Smoke task on aerial-cactus only — fastest, cheapest end-to-end check.
task-test:
	@for i in $(INTERFACES); do \
		echo "=== interface=$$i task=aerial-cactus-identification reps=$(REPS) ==="; \
		for n in $$(seq 1 $(REPS)); do \
			uv run coordinator --task aerial-cactus-identification --interface $$i $(RUN_FLAGS) \
				|| echo "errored, continuing"; \
		done; \
		[ "$$i" = "local" ] || uv run cell-feedback --task aerial-cactus-identification --interface $$i $(CELL_FLAGS) || true; \
	done

# Full extra-small experiment: all 8 selected competitions once per interface.
# With REPS=3, each (interface × task) is repeated 3 times. Expect ~REPS × 1-2h
# wall clock and ~REPS × $5-15 in tokens per interface.
task-xs:
	@echo "=== checking Kaggle ToS for all $(words $(TASKS_XS)) competitions ==="
	@uv run check-kaggle-rules $(TASKS_XS)
	@for i in $(INTERFACES); do \
		echo "=== interface=$$i tasks=$(words $(TASKS_XS)) reps=$(REPS) ==="; \
		for t in $(TASKS_XS); do \
			echo "=== preparing $$t ==="; \
			uv run prepare-task $$t || { echo "ABORT: prepare failed for $$t"; exit 1; }; \
			for n in $$(seq 1 $(REPS)); do \
				uv run coordinator --task $$t --interface $$i $(RUN_FLAGS) \
					|| echo "$$t/$$i errored, continuing"; \
			done; \
			[ "$$i" = "local" ] || uv run cell-feedback --task $$t --interface $$i $(CELL_FLAGS) || true; \
		done; \
	done

# MLE-bench Low complexity split (22 competitions, ~158GB on disk). Each
# competition requires its rules accepted on Kaggle first. With REPS>1 each
# (interface × task) is repeated. Expect roughly REPS × 5-7h wall clock and
# ~REPS × $25-50 in tokens per interface.
task-s:
	@for i in $(INTERFACES); do \
		echo "=== interface=$$i tasks=$(words $(TASKS_S)) reps=$(REPS) ==="; \
		for t in $(TASKS_S); do \
			echo "=== preparing $$t ==="; \
			uv run prepare-task $$t || { echo "skip $$t (prepare failed)"; continue; }; \
			for n in $$(seq 1 $(REPS)); do \
				uv run coordinator --task $$t --interface $$i $(RUN_FLAGS) \
					|| echo "$$t/$$i errored, continuing"; \
			done; \
			[ "$$i" = "local" ] || uv run cell-feedback --task $$t --interface $$i $(CELL_FLAGS) || true; \
		done; \
	done

# --- interface lifecycle -----------------------------------------------------
# Generate an interface (CLI or MCP) from a docs source. DOCS may be a local
# path or a GitHub URL; URLs are cloned into .local/docs/<repo>/ first.
#   make interface-build TYPE=cli NAME=hops DOCS=https://github.com/logicalclocks/logicalclocks.github.io
interface-build:
	@if [ -z "$(TYPE)" ] || [ -z "$(NAME)" ] || [ -z "$(DOCS)" ]; then \
		echo "usage: make interface-build TYPE=<cli|mcp> NAME=<name> DOCS=<github-url-or-path>"; \
		exit 1; \
	fi
	bash scripts/interface/build.sh $(TYPE) $(NAME) $(DOCS)

# Install an interface so the agent can reach it. `SOURCE` is auto-classified:
#   no SOURCE        → use a pre-built interfaces/<name>-<type>/
#   SOURCE=path/     → editable install from a local directory
#   SOURCE=git+...   → install from a git URL
#   SOURCE=<pkg>     → install from PyPI
# `BIN=` overrides the binary / import name we probe with after install
# (defaults to NAME for cli/sdk, NAME-mcp for mcp).
#
#   make interface-install TYPE=cli NAME=hops
#   make interface-install TYPE=cli NAME=hops SOURCE=../my-hops-cli
#   make interface-install TYPE=mcp NAME=hops SOURCE=git+https://github.com/me/hops-mcp
#   make interface-install TYPE=sdk NAME=hopsworks SOURCE=hopsworks
interface-install:
	@if [ -z "$(TYPE)" ] || [ -z "$(NAME)" ]; then \
		echo "usage: make interface-install TYPE=<cli|mcp|sdk> NAME=<name> [SOURCE=...] [BIN=...]"; \
		exit 1; \
	fi
	bash scripts/interface/install.sh $(TYPE) $(NAME) "$(SOURCE)" "$(BIN)"

interface-uninstall:
	@if [ -z "$(TYPE)" ] || [ -z "$(NAME)" ]; then \
		echo "usage: make interface-uninstall TYPE=<cli|mcp|sdk> NAME=<name>"; \
		exit 1; \
	fi
	bash scripts/interface/uninstall.sh $(TYPE) $(NAME)

# Verify built interfaces are installed and reachable by the agent. Optional
# TYPE / NAME narrow the check; without them every interfaces/<name>-<type>
# is tested.
interface-test:
	bash scripts/interface/test.sh $(TYPE) $(NAME)

# Reset workspaces but keep results.csv. Drops per-cell directories under
# results/, the events log, and the MLE-bench diskcache.
clean:
	bash scripts/stop.sh || true
	find results -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
	rm -rf .local/litellm/events.jsonl cache

# clean + remove results.csv. Keeps .venv, .local/mle-bench-data, .kaggle.
reset:
	bash scripts/stop.sh || true
	rm -rf results/* .local/litellm/events.jsonl cache

# reset + remove .local/ (MLE-bench datasets, doc clones) and .venv.
reset-all:
	bash scripts/stop.sh || true
	rm -rf results/* .local .venv cache
