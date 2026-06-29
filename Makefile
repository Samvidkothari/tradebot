# tradebot — common dev tasks.  Run `make check` before every deploy/restart.
.PHONY: install test smoke check run restart

install:
	.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

test:          ## run the unit + route test suite
	.venv/bin/pytest -q

smoke:         ## hit every route in-process and assert 200
	.venv/bin/python smoke_test.py

check: test smoke   ## the pre-deploy gate: tests + route smoke
	@echo "✓ all checks passed"

run:           ## start the dashboard
	.venv/bin/python dashboard.py

restart:       ## stop any running dashboard and relaunch
	./restart_dashboard.command
