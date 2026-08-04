.PHONY: install test demo doctor serve mcp enrich clean-demo

install:
	python3 -m pip install -e .

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

doctor:
	PYTHONPATH=src python3 -m papertrail doctor

demo:
	PYTHONPATH=src python3 -m papertrail --home .papertrail init
	PYTHONPATH=src python3 -m papertrail --home .papertrail add-text examples/recovery_agents.txt --title "Recovery-Aware Agents" --authors "A. Researcher, B. Builder" --published 2026-07-15 --source-url https://example.org/recovery-aware --source-class synthetic
	PYTHONPATH=src python3 -m papertrail --home .papertrail snapshot create local-demo
	PYTHONPATH=src python3 -m papertrail --home .papertrail search "tool failure recovery" --snapshot local-demo

serve:
	PYTHONPATH=src python3 -m papertrail serve

mcp:
	PYTHONPATH=src python3 -m papertrail mcp

enrich:
	PYTHONPATH=src python3 -m papertrail enrich

clean-demo:
	@echo "Remove .papertrail manually if you want to reset local data."
