SCENARIO ?= scenarios/public_01_dev.yaml
NAME ?= $(basename $(notdir $(SCENARIO)))
PY ?= .venv/bin/python

setup:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
baseline:
	$(PY) -m sim baseline --scenario $(SCENARIO) --run-dir runs/base_$(NAME)
serve:
	$(PY) -m sim serve --scenario $(SCENARIO) --run-dir runs/mine_$(NAME) --force
grade:
	mkdir -p reports && $(PY) -m sim grade --run runs/mine_$(NAME) --baseline runs/base_$(NAME) --out reports/$(NAME).md
# fresh sim + fresh controller state + grade, in one go
run:
	scripts/run_scenario.sh $(SCENARIO) $(NAME)
all-public:
	scripts/run_scenario.sh scenarios/public_01_dev.yaml public_01
	scripts/run_scenario.sh scenarios/public_02_incidents.yaml public_02
	scripts/run_scenario.sh scenarios/public_03_load.yaml public_03
	$(PY) -m controller.audit --db state/public_03/controller.db health > reports/public_03_loop_health.json
crash-test:
	scripts/crash_test.sh scenarios/public_02_incidents.yaml 10
view:
	$(PY) -m controller.view --db state/$(NAME)/controller.db
test:
	$(PY) -m pytest -q tests
